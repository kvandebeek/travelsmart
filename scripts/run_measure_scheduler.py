"""Measure chains around the clock, inside each provider's budget (docs/network-design.md §6.5).

Collection runs continuously and paces itself: a day's allowance spread evenly over the day, rather
than spent as fast as the cap allows. A day of measurements taken in one morning would describe the
morning, not the day.

Where it got to is stored in the database, not in memory, so a restart continues through the chain
list instead of measuring the first few over and over. Stop it with Ctrl-C; nothing is left half
written, because a chain's legs are committed together.
"""

from __future__ import annotations

import argparse
import json
import random
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from travelsmart.db import SourceState, make_session_factory
from travelsmart.measure.budget import BUDGETS, remaining_today
from travelsmart.measure.chains import chain_waypoints
from travelsmart.measure.collect import rows_for_chain, store
from travelsmart.providers import here, tomtom
from scripts.measure_chains import load_env

MODULES = {"tomtom": tomtom, "here": here}
running = True


def stop(*_args) -> None:
    global running
    running = False
    print("\nfinishing the current request and stopping", file=sys.stderr, flush=True)


def cursor(session, provider: str) -> int:
    row = session.get(SourceState, f"{provider}_chain_cursor")
    return int(row.value) if row else 0


def save_cursor(session, provider: str, value: int) -> None:
    key = f"{provider}_chain_cursor"
    row = session.get(SourceState, key)
    if row:
        row.value = str(value)
    else:
        session.add(SourceState(key=key, value=str(value)))
    session.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--providers", nargs="+", default=["tomtom"], choices=sorted(MODULES))
    parser.add_argument("--chains", type=Path, default=Path("data/network/chains.json"))
    parser.add_argument("--network", type=Path, default=Path("data/network/network.json"))
    parser.add_argument("--hours", type=float, default=0, help="stop after this long (0 = forever)")
    args = parser.parse_args()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    load_env()
    network = json.loads(args.network.read_text(encoding="utf-8"))
    chains = json.loads(args.chains.read_text(encoding="utf-8"))["chains"]
    sessions = make_session_factory()
    keys = {"tomtom": tomtom.api_keys() if "tomtom" in args.providers else [],
            "here": [here.api_key()] if "here" in args.providers else []}
    pace = {provider: BUDGETS[provider].seconds_between_requests() for provider in args.providers}
    for provider in args.providers:
        print(f"{provider}: {BUDGETS[provider].daily_allowance()} requests a day, "
              f"one every {pace[provider] / 60:.1f} min", file=sys.stderr)

    started = time.monotonic()
    next_due = {provider: started for provider in args.providers}
    measured = {provider: 0 for provider in args.providers}
    while running:
        if args.hours and time.monotonic() - started >= args.hours * 3600:
            break
        provider = min(args.providers, key=lambda name: next_due[name])
        wait = next_due[provider] - time.monotonic()
        if wait > 0:
            time.sleep(min(wait, 5))      # in short naps, so Ctrl-C is felt quickly
            continue
        with sessions() as session:
            if remaining_today(session, provider) <= 0:
                print(f"{provider}: budget spent, pausing an hour", file=sys.stderr, flush=True)
                next_due[provider] = time.monotonic() + 3600
                continue
            index = cursor(session, provider)
        chain = chains[index % len(chains)]
        waypoints = chain_waypoints(chain, network["nodes"], network["edges"])
        module = MODULES[provider]
        waypoints = waypoints[:module.MAX_WAYPOINTS]
        next_due[provider] = time.monotonic() + pace[provider]
        if len(waypoints) < 2:
            with sessions() as session:
                save_cursor(session, provider, index + 1)
            continue
        requested_at = datetime.now(timezone.utc)
        try:
            legs = module.measure_chain(waypoints, key=random.choice(keys[provider]))
        except Exception as error:
            print(f"{provider} {chain['id']}: {type(error).__name__}: {error}",
                  file=sys.stderr, flush=True)
            with sessions() as session:
                save_cursor(session, provider, index + 1)
            continue
        with sessions() as session:
            result = store(session, rows_for_chain(chain, legs, network["edges"], provider,
                                                   requested_at))
            save_cursor(session, provider, index + 1)
        measured[provider] += 1
        print(f"{datetime.now().strftime('%H:%M:%S')} {provider} {chain['id']} "
              f"({index % len(chains) + 1}/{len(chains)}): {result.accepted} accepted, "
              f"{result.rejected} rejected", file=sys.stderr, flush=True)

    print(f"stopped after {dict(measured)} requests", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
