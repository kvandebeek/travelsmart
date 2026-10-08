"""Collect adjacent Google Maps route times along Belgian corridor chains."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from collect_google_maps import LOCAL_TIMEZONE, ROOT, Point, capture, load_points


@dataclass(frozen=True)
class Corridor:
    id: str
    name: str
    nodes: tuple[str, ...]


@dataclass(frozen=True)
class Leg:
    corridor: Corridor
    direction: str
    index: int
    count: int
    origin: Point
    destination: Point


def default_storage_dirs(corridor_ids: list[str] | None, direction: str,
                         run_id: str | None) -> tuple[Path, Path]:
    """Give independent stretch selections their own capture and browser folders."""
    if not corridor_ids and direction == "both" and not run_id:
        return ROOT / "data" / "google_maps_stretches", ROOT / "data" / "google_maps_stretches_profile"
    selection = "_".join(sorted(set(corridor_ids))) if corridor_ids else "all"
    slug = re.sub(r"[^a-z0-9_-]+", "_", selection.lower()).strip("_")
    slug = f"{slug}_{direction}"
    if len(slug) > 64:
        digest = hashlib.sha256(slug.encode("utf-8")).hexdigest()[:12]
        slug = f"group_{digest}_{direction}"
    if run_id:
        label = re.sub(r"[^a-z0-9_-]+", "_", run_id.lower()).strip("_")
        if not label:
            raise ValueError("run ID needs at least one letter or digit")
        if len(label) > 32:
            digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:12]
            label = f"{label[:20]}_{digest}"
        slug += f"_{label}"
    return (ROOT / "data" / f"google_maps_stretches_{slug}_captures",
            ROOT / "data" / f"google_maps_stretches_{slug}_profile")


def load_corridors(path: Path, points: dict[str, Point]) -> list[Corridor]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    rows = data.get("corridors") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError("corridors file must contain a nonempty corridors list")
    corridors = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("id") or not row.get("name"):
            raise ValueError("each corridor needs an id and name")
        nodes = row.get("nodes")
        if not isinstance(nodes, list) or len(nodes) < 2 or len(set(nodes)) != len(nodes):
            raise ValueError(f"corridor {row['id']} needs at least two distinct nodes")
        missing = set(nodes) - points.keys()
        if missing:
            raise ValueError(f"corridor {row['id']} references unknown points: {', '.join(sorted(missing))}")
        corridors.append(Corridor(row["id"], row["name"], tuple(nodes)))
    if len({corridor.id for corridor in corridors}) != len(corridors):
        raise ValueError("corridor IDs must be unique")
    return corridors


def corridor_legs(corridor: Corridor, points: dict[str, Point], direction: str) -> list[Leg]:
    nodes = corridor.nodes if direction == "forward" else corridor.nodes[::-1]
    return [Leg(corridor, direction, index + 1, len(nodes) - 1,
                points[origin], points[destination])
            for index, (origin, destination) in enumerate(zip(nodes, nodes[1:]))]


def append_summary(output_dir: Path, corridor: Corridor, direction: str,
                   corridor_run_id: str, sweep_id: str, legs: list[Leg], records: list[dict]) -> dict:
    complete = len(records) == len(legs) and all(row["status"] == "ok" for row in records)
    first = records[0]["captured_at"] if records else None
    last = records[-1]["captured_at"] if records else None
    summary = {
        "corridor_id": corridor.id,
        "corridor_name": corridor.name,
        "direction": direction,
        "corridor_run_id": corridor_run_id,
        "sweep_id": sweep_id,
        "origin_id": legs[0].origin.id,
        "destination_id": legs[-1].destination.id,
        "leg_count": len(legs),
        "successful_leg_count": sum(row["status"] == "ok" for row in records),
        "status": "complete" if complete else "incomplete",
        "first_captured_at": first,
        "last_captured_at": last,
        "measurement_span_seconds": round((datetime.fromisoformat(last) - datetime.fromisoformat(first)).total_seconds())
                                    if first and last else None,
        "sum_of_live_leg_minutes": sum(row["travel_time_minutes"] for row in records) if complete else None,
        "sum_of_leg_distance_km": round(sum(row["distance_km"] for row in records), 1)
                                   if complete and all(row["distance_km"] is not None for row in records) else None,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "summaries.jsonl").open("a", encoding="utf-8") as file:
        file.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corridor", action="append", help="Run only this corridor ID; repeat to select several")
    parser.add_argument("--direction", choices=("both", "forward", "reverse"), default="both")
    parser.add_argument("--run-id", help="Label for a parallel copy of the same corridor selection")
    parser.add_argument("--points-file", type=Path, default=ROOT / "config" / "google_maps_points.csv")
    parser.add_argument("--extra-points-file", type=Path,
                        default=ROOT / "config" / "google_maps_stretch_points.csv")
    parser.add_argument("--corridors-file", type=Path,
                        default=ROOT / "config" / "google_maps_stretch_corridors.yaml")
    parser.add_argument("--delay-seconds", type=float, default=1, help="Pause between route checks (default: 1)")
    parser.add_argument("--jitter-seconds", type=float, default=0)
    parser.add_argument("--settle-seconds", type=float, default=2)
    parser.add_argument("--interval-minutes", type=float, default=60,
                        help="Minimum time between sweep starts when repeating (default: 60)")
    parser.add_argument("--timeout-seconds", type=int, default=45)
    parser.add_argument("--once", action="store_true", help="Run one full sweep and exit")
    parser.add_argument("--dry-run", action="store_true", help="Show planned checks without opening a browser")
    parser.add_argument("--screenshots", action="store_true", help="Also save labeled PNGs")
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True,
                        help="Hide the browser window (default); --no-headless shows it")
    parser.add_argument("--output-dir", type=Path, help="Capture folder; defaults to one per selection")
    parser.add_argument("--profile-dir", type=Path, help="Browser profile; defaults to one per selection")
    args = parser.parse_args()
    if (args.delay_seconds < 0 or args.jitter_seconds < 0 or args.settle_seconds < 0
            or args.interval_minutes <= 0 or args.timeout_seconds <= 0):
        parser.error("interval and timeout must be positive; delays cannot be negative")
    try:
        point_rows = load_points(args.points_file) + load_points(args.extra_points_file)
        if len({point.id for point in point_rows}) != len(point_rows):
            raise ValueError("point IDs must be unique across both point files")
        points = {point.id: point for point in point_rows}
        corridors = load_corridors(args.corridors_file, points)
    except (OSError, ValueError, yaml.YAMLError) as error:
        parser.error(str(error))
    if args.corridor:
        selected = set(args.corridor)
        unknown = selected - {corridor.id for corridor in corridors}
        if unknown:
            parser.error(f"unknown corridors: {', '.join(sorted(unknown))}")
        corridors = [corridor for corridor in corridors if corridor.id in selected]

    try:
        default_output, default_profile = default_storage_dirs(args.corridor, args.direction, args.run_id)
    except ValueError as error:
        parser.error(str(error))
    args.output_dir = args.output_dir or default_output
    args.profile_dir = args.profile_dir or default_profile
    if args.output_dir.resolve() == args.profile_dir.resolve():
        parser.error("output and profile directories must differ")

    directions = ("forward", "reverse") if args.direction == "both" else (args.direction,)
    groups = [(corridor, direction, corridor_legs(corridor, points, direction))
              for corridor in corridors for direction in directions]
    check_count = sum(len(legs) for _, _, legs in groups)
    if args.dry_run:
        print(json.dumps({"corridors": len(corridors), "directed_legs": check_count,
                          "output_dir": str(args.output_dir.resolve()),
                          "profile_dir": str(args.profile_dir.resolve()),
                          "minimum_pause_minutes": round((check_count - 1) * args.delay_seconds / 60, 1),
                          "by_corridor": {corridor.id: (len(corridor.nodes) - 1) * len(directions)
                                          for corridor in corridors}}))
        return 0

    args.profile_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        try:
            context = playwright.chromium.launch_persistent_context(
                str(args.profile_dir.resolve()), headless=args.headless,
                viewport={"width": 1440, "height": 900}, locale="en-GB",
                timezone_id="Europe/Brussels",
            )
        except PlaywrightError as error:
            if "Opening in existing browser session" in str(error):
                print(f"Browser profile is already in use: {args.profile_dir.resolve()}\n"
                      "Use a distinct --run-id or --profile-dir for another simultaneous run.",
                      file=sys.stderr)
            else:
                print(f"Could not start Chromium: {error}", file=sys.stderr)
            return 2
        page = context.pages[0] if context.pages else context.new_page()
        try:
            while True:
                started = time.monotonic()
                sweep_id = datetime.now(LOCAL_TIMEZONE).isoformat()
                work_items = [(corridor.id, direction, leg)
                              for corridor, direction, legs in groups for leg in legs]
                random.shuffle(work_items)
                run_ids = {(corridor.id, direction): datetime.now(LOCAL_TIMEZONE).isoformat()
                           for corridor, direction, _ in groups}
                group_records = {key: [] for key in run_ids}
                failed = False
                for pair_index, (corridor_id, direction, leg) in enumerate(work_items, start=1):
                    if pair_index > 1:
                        time.sleep(args.delay_seconds + random.uniform(0, args.jitter_seconds))
                    key = (corridor_id, direction)
                    try:
                        record = capture(
                            page, origin=leg.origin, destination=leg.destination,
                            output_dir=args.output_dir, timeout_seconds=args.timeout_seconds,
                            headed=not args.headless, settle_seconds=args.settle_seconds,
                            screenshots=args.screenshots, sweep_id=sweep_id,
                            pair_index=pair_index, pair_count=check_count,
                            metadata={"corridor_id": corridor_id, "corridor_name": leg.corridor.name,
                                      "corridor_run_id": run_ids[key],
                                      "direction": direction, "leg_index": leg.index,
                                      "leg_count": leg.count},
                        )
                        print(json.dumps(record, ensure_ascii=True), flush=True)
                        group_records[key].append(record)
                        failed |= record["status"] != "ok"
                    except PlaywrightError as error:
                        failed = True
                        print(f"Capture failed for {corridor_id} {direction} leg {leg.index}: {error}",
                              file=sys.stderr, flush=True)
                for corridor, direction, legs in groups:
                    key = (corridor.id, direction)
                    summary = append_summary(args.output_dir, corridor, direction,
                                             run_ids[key], sweep_id, legs, group_records[key])
                    print(json.dumps({"summary": summary}, ensure_ascii=True), flush=True)
                if args.once:
                    return 1 if failed else 0
                pause = max(args.delay_seconds + random.uniform(0, args.jitter_seconds),
                            args.interval_minutes * 60 - (time.monotonic() - started))
                time.sleep(pause)
        except KeyboardInterrupt:
            return 0
        finally:
            context.close()


if __name__ == "__main__":
    raise SystemExit(main())
