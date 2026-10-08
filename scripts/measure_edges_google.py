"""Measure network edges with the Google Maps browser collector (docs/network-design.md §6.2).

Google measures one edge per search, from the edge's own start point on the carriageway to its own
end point, so the search drives exactly that stretch in that direction. Unlike TomTom and HERE,
Google names the roads it used, so §4's road check applies here and not only the length check: a
route that went around over another road is rejected rather than filed under this edge.

One runner is one Chromium profile. Several runners may work at once only with separate profile
directories; Chromium refuses to share one.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import Error as PlaywrightError, sync_playwright

from scripts.collect_google_maps import LOCAL_TIMEZONE, Point, capture, fresh_page
from travelsmart.db import LegMeasurement, make_session_factory
from travelsmart.measure.chains import measurable
from travelsmart.measure.collect import half_hour_of
from travelsmart.measure.validation import accepts

def edge_points(edge_id: str, edge: dict) -> tuple[Point, Point]:
    """The edge's own ends, as the origin and destination of one directions search."""
    start, end = edge["start"], edge["end"]
    roads = "/".join(edge.get("roads", [])) or edge["kind"]
    return (Point(id=f"{edge_id}:a", name=f"{roads} start", location=f"{start[0]:.6f},{start[1]:.6f}"),
            Point(id=f"{edge_id}:b", name=f"{roads} end", location=f"{end[0]:.6f},{end[1]:.6f}"))


def row_for(edge_id: str, edge: dict, record: dict) -> LegMeasurement | None:
    """One measurement row from a capture, or None when Google showed no route at all."""
    if record.get("status") != "ok" or record.get("travel_time_minutes") is None:
        return None
    moment = datetime.now(timezone.utc)
    weekday, half_hour = half_hour_of(moment)
    metres = record["distance_km"] * 1000 if record.get("distance_km") is not None else None
    via = record.get("via") or ""
    if metres is None:
        ok, why = False, "Google listed no distance"
    else:
        ok, why = accepts(edge, metres, text=via)
    return LegMeasurement(
        edge_id=edge_id, provider="google", chain_id=None, departure_utc=moment,
        weekday=weekday, half_hour=half_hour,
        # Google's cards round to whole minutes; §12 wants exact seconds from the response instead.
        seconds=record["travel_time_minutes"] * 60.0,
        free_flow_seconds=None, typical_seconds=None, metres=metres,
        roads=via or None, accepted=ok, rejected_because=why or None, created_at=moment)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", type=Path, default=Path("data/network/network.json"))
    parser.add_argument("--limit", type=int, required=True, help="how many edges to measure now")
    parser.add_argument("--runner", type=int, default=0, help="which slice of the edges this runner takes")
    parser.add_argument("--runners", type=int, default=1, help="how many runners share the work")
    parser.add_argument("--profile-dir", type=Path, default=Path("data/google_profile_edges"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/google_edge_captures"))
    parser.add_argument("--delay-seconds", type=float, default=4.0)
    parser.add_argument("--jitter-seconds", type=float, default=3.0)
    parser.add_argument("--timeout-seconds", type=int, default=45)
    parser.add_argument("--settle-seconds", type=float, default=1.5)
    parser.add_argument("--headless", action="store_true", default=True)
    parser.add_argument("--seed", type=int, default=20261009)
    # Google reports whole minutes (§12), so 30 seconds of rounding is 25% of a two-minute edge and
    # 4% of a twelve-minute one. Pointing it at the longer stretches keeps that error small, and
    # leaves the short ones to TomTom and HERE, which answer in seconds.
    parser.add_argument("--min-metres", type=float, default=2000)
    args = parser.parse_args()

    network = json.loads(args.network.read_text(encoding="utf-8"))
    targets = [(edge_id, edge) for edge_id, edge in network["edges"].items()
               if measurable(edge) and edge["metres"] >= args.min_metres]
    # A stable shuffle, then one slice per runner: every runner measures different edges, and a
    # restart with the same seed picks up the same ordering rather than re-measuring the first few.
    random.Random(args.seed).shuffle(targets)
    mine = targets[args.runner::args.runners][:args.limit]
    if not mine:
        print("no edges to measure", file=sys.stderr)
        return 0

    sessions = make_session_factory()
    args.profile_dir.mkdir(parents=True, exist_ok=True)
    accepted = rejected = missed = 0
    with sync_playwright() as playwright:
        try:
            context = playwright.chromium.launch_persistent_context(
                str(args.profile_dir.resolve()), headless=args.headless,
                viewport={"width": 1440, "height": 900}, locale="en-GB",
                timezone_id="Europe/Brussels")
        except PlaywrightError as error:
            print(f"Could not start Chromium: {error}", file=sys.stderr)
            return 2
        page = context.pages[0] if context.pages else context.new_page()
        sweep_id = datetime.now(LOCAL_TIMEZONE).isoformat()
        try:
            for index, (edge_id, edge) in enumerate(mine):
                if index:
                    time.sleep(args.delay_seconds + random.uniform(0, args.jitter_seconds))
                origin, destination = edge_points(edge_id, edge)
                try:
                    page = fresh_page(context, page)
                    record = capture(page, origin=origin, destination=destination,
                                     output_dir=args.output_dir, timeout_seconds=args.timeout_seconds,
                                     headed=not args.headless, settle_seconds=args.settle_seconds,
                                     screenshots=False, sweep_id=sweep_id, pair_index=index + 1,
                                     pair_count=len(mine), metadata={"edge_id": edge_id,
                                                                     "edge_kind": edge["kind"],
                                                                     "expected_metres": edge["metres"]})
                except PlaywrightError as error:
                    missed += 1
                    print(f"{edge_id}: {error}", file=sys.stderr, flush=True)
                    continue
                row = row_for(edge_id, edge, record)
                if row is None:
                    missed += 1
                    print(f"{edge_id}: no route ({record.get('status')})", file=sys.stderr, flush=True)
                    continue
                with sessions() as session:
                    session.add(row)
                    session.commit()
                accepted += row.accepted
                rejected += not row.accepted
                print(f"{edge_id} {edge['kind']}: {row.seconds / 60:.0f} min, "
                      f"{(row.metres or 0) / 1000:.1f} km via {row.roads or '-'} "
                      f"{'ok' if row.accepted else 'REJECTED ' + (row.rejected_because or '')}",
                      file=sys.stderr, flush=True)
        except KeyboardInterrupt:
            pass
        finally:
            context.close()
    print(f"\nrunner {args.runner}/{args.runners}: {accepted} accepted, {rejected} rejected, "
          f"{missed} without a route", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
