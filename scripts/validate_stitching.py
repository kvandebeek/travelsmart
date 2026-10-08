"""Does a route stitched from edges match the same trip measured whole? (§9, Validation)

The product answers a trip by chaining edge measurements. That only holds if the chain adds up to
what a provider says the whole trip takes, so a rotating sample of A -> B trips is measured directly
and compared with the stitched estimate.

A difference has two causes worth telling apart: the provider may have driven another way than the
graph's route, which shows up as a difference in distance, or it may agree on the way and disagree
on the time. Both are reported, because only the second is about measurement.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from travelsmart.db import make_session_factory
from travelsmart.measure.budget import remaining_today
from travelsmart.measure.routing import fastest_route, minute_of_week
from travelsmart.providers import tomtom
from scripts.measure_chains import load_env
from scripts.plan_trip import load_profiles


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, required=True, help="how many trips to check")
    parser.add_argument("--network", type=Path, default=Path("data/network/network.json"))
    parser.add_argument("--profiles", type=Path, default=Path("data/network/profiles.json"))
    parser.add_argument("--min-km", type=float, default=15, help="skip trips shorter than this")
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()

    load_env()
    network = json.loads(args.network.read_text(encoding="utf-8"))
    nodes, edges = network["nodes"], network["edges"]
    profiles = load_profiles(args.profiles)
    keys = tomtom.api_keys()

    with make_session_factory()() as session:
        left = remaining_today(session, "tomtom")
    if left < args.limit:
        print(f"only {left} TomTom requests left today", file=sys.stderr)
        args.limit = left
    if args.limit <= 0:
        return

    # Only nodes a route can actually leave from and arrive at.
    usable = [node_id for node_id in nodes if any(edge["from"] == node_id for edge in edges.values())]
    chooser = random.Random(args.seed)
    now = datetime.now()
    minute = minute_of_week(now.weekday(), now.hour * 60 + now.minute)

    differences, checked = [], 0
    while checked < args.limit and usable:
        start, end = chooser.sample(usable, 2)
        route = fastest_route(edges, profiles, start, end, minute)
        if route is None or len(route.edges) < 2:
            continue
        stitched_metres = sum(edges[edge_id]["metres"] for edge_id in route.edges)
        if stitched_metres < args.min_km * 1000:
            continue
        checked += 1
        waypoints = [tuple(edges[route.edges[0]]["start"]), tuple(edges[route.edges[-1]]["end"])]
        try:
            legs = tomtom.measure_chain(waypoints, key=keys[checked % len(keys)])
        except Exception as error:
            print(f"{start} -> {end}: {type(error).__name__}: {error}", file=sys.stderr)
            continue
        if not legs:
            continue
        direct = legs[0]
        difference = route.seconds / direct.seconds - 1 if direct.seconds else 0.0
        distance_difference = stitched_metres / direct.metres - 1 if direct.metres else 0.0
        differences.append((difference, distance_difference, route.measured_share))
        print(f"{start} -> {end}: stitched {route.seconds / 60:5.1f} min over "
              f"{stitched_metres / 1000:5.1f} km, direct {direct.seconds / 60:5.1f} min over "
              f"{direct.metres / 1000:5.1f} km | time {difference:+.0%}, way {distance_difference:+.0%}, "
              f"{route.measured_share:.0%} measured", file=sys.stderr)

    if not differences:
        print("nothing comparable was measured", file=sys.stderr)
        return
    times = [value for value, _, _ in differences]
    ways = [value for _, value, _ in differences]
    print(f"\n{len(differences)} trips: stitched time is {statistics.median(times):+.0%} against "
          f"direct (median), spread {min(times):+.0%} to {max(times):+.0%}", file=sys.stderr)
    print(f"the graph's way is {statistics.median(ways):+.0%} longer than the one driven (median); "
          f"a large value here means the routes differ, not the times", file=sys.stderr)
    measured = statistics.mean(share for _, _, share in differences)
    print(f"{measured:.0%} of the stitched routes rested on measurements rather than free flow",
          file=sys.stderr)


if __name__ == "__main__":
    main()
