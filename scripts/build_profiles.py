"""Turn the raw leg measurements into a published profile per edge (docs/network-design.md §7, §8).

The raw rows are the firehose and stay on the collector machine. What is published is small: per
edge, the merged 7 x 48 cells, the usual time and how far the providers disagreed. The file is
replaced rather than appended, so it stays a few megabytes however long collection runs.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from travelsmart.db import LegMeasurement, make_session_factory
from travelsmart.measure.profiles import build_profile, combine


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weeks", type=int, default=8, help="rolling window to publish")
    parser.add_argument("--output", type=Path, default=Path("data/network/profiles.json"))
    parser.add_argument("--min-samples", type=int, default=1,
                        help="skip an edge with fewer accepted measurements than this")
    args = parser.parse_args()

    since = datetime.now(timezone.utc) - timedelta(weeks=args.weeks)
    sessions = make_session_factory()
    with sessions() as session:
        rows = session.scalars(
            select(LegMeasurement).where(LegMeasurement.accepted.is_(True),
                                         LegMeasurement.departure_utc >= since)).all()
    if not rows:
        print("no accepted measurements in the window", file=sys.stderr)
        return

    samples: dict[tuple[str, str], list[tuple[int, int, float]]] = defaultdict(list)
    for row in rows:
        samples[(row.edge_id, row.provider)].append((row.weekday, row.half_hour, row.seconds))

    per_edge: dict[str, list] = defaultdict(list)
    for (edge_id, provider), values in samples.items():
        per_edge[edge_id].append(build_profile(edge_id, provider, values))

    profiles = {}
    for edge_id, edge_profiles in per_edge.items():
        if sum(len(p.cells) for p in edge_profiles) < args.min_samples:
            continue
        profiles[edge_id] = combine(edge_profiles)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(".partial")
    with temp.open("w", encoding="utf-8") as file:
        json.dump({"built_at": datetime.now(timezone.utc).isoformat(), "weeks": args.weeks,
                   "profiles": profiles}, file, separators=(",", ":"))
    temp.replace(args.output)

    providers = Counter(row.provider for row in rows)
    covered = sum(1 for profile in profiles.values() if profile.get("cells"))
    print(f"{len(rows):,} accepted measurements over {args.weeks} weeks from {dict(providers)}",
          file=sys.stderr)
    print(f"{covered:,} edges have a profile, written to {args.output} "
          f"({args.output.stat().st_size / 1_000_000:.1f} MB)", file=sys.stderr)


if __name__ == "__main__":
    main()
