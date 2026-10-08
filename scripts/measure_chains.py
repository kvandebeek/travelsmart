"""Measure chains with TomTom or HERE and store every leg (docs/network-design.md §6, §7).

Keys come from the environment, or from a local .env that is never committed. Both providers have a
hard free-tier budget, so --limit is required rather than defaulted: a run measures exactly as many
chains as it is told to.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from travelsmart.db import make_session_factory
from travelsmart.measure.chains import chain_waypoints
from travelsmart.measure.collect import Stored, rows_for_chain, store
from travelsmart.providers import here, tomtom


def load_env(path: Path = Path(".env")) -> None:
    """Read KEY=value lines into the environment without overwriting what is already set."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("tomtom", "here"), default="tomtom")
    parser.add_argument("--limit", type=int, required=True, help="how many chains to measure now")
    parser.add_argument("--offset", type=int, default=0, help="where in the chain list to start")
    parser.add_argument("--pause", type=float, default=2.0, help="seconds between requests")
    parser.add_argument("--chains", type=Path, default=Path("data/network/chains.json"))
    parser.add_argument("--network", type=Path, default=Path("data/network/network.json"))
    parser.add_argument("--dry-run", action="store_true", help="build the requests but call nothing")
    args = parser.parse_args()

    load_env()
    network = json.loads(args.network.read_text(encoding="utf-8"))
    chains = json.loads(args.chains.read_text(encoding="utf-8"))["chains"]
    chosen = chains[args.offset:args.offset + args.limit]
    if not chosen:
        print("no chains in that range", file=sys.stderr)
        return

    module = tomtom if args.provider == "tomtom" else here
    keys = tomtom.api_keys() if args.provider == "tomtom" else [here.api_key()]
    sessions = make_session_factory()
    totals, failures = Stored(), Counter()

    for index, chain in enumerate(chosen):
        waypoints = chain_waypoints(chain, network["nodes"])[:module.MAX_WAYPOINTS]
        if len(waypoints) < 2:
            continue
        if args.dry_run:
            print(f"{chain['id']}: {len(waypoints)} waypoints, {len(chain['edges'])} legs, "
                  f"{chain['metres'] / 1000:.1f} km", file=sys.stderr)
            continue
        requested_at = datetime.now(timezone.utc)
        try:
            legs = module.measure_chain(waypoints, key=keys[index % len(keys)])
        except Exception as error:                       # one bad chain must not end the run
            failures[type(error).__name__] += 1
            print(f"{chain['id']}: {type(error).__name__}: {error}", file=sys.stderr)
            time.sleep(args.pause)
            continue
        with sessions() as session:
            result = store(session, rows_for_chain(chain, legs, network["edges"], args.provider,
                                                   requested_at))
        totals.accepted += result.accepted
        totals.rejected += result.rejected
        print(f"{chain['id']}: {len(legs)} legs, {result.accepted} accepted, "
              f"{result.rejected} rejected", file=sys.stderr)
        if index + 1 < len(chosen):
            time.sleep(args.pause)

    if not args.dry_run:
        print(f"\n{len(chosen)} chains requested with {args.provider}: "
              f"{totals.accepted:,} legs accepted, {totals.rejected:,} rejected", file=sys.stderr)
        if failures:
            print(f"failed requests: {dict(failures)}", file=sys.stderr)


if __name__ == "__main__":
    main()
