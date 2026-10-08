"""Read live Google Maps driving directions in a local browser.

This is an optional, standalone collector. It does not use a Maps API key.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from itertools import permutations
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
LOCAL_TIMEZONE = ZoneInfo("Europe/Brussels")
TRAVEL_TIME = re.compile(
    r"(?:(?:\d+\s*(?:h|hr|hrs|hour|hours|u|uur)\s*)?\d+\s*"
    r"(?:min|mins|minute|minutes)\b|\d+\s*(?:h|hr|hrs|hour|hours|u|uur)\b)",
    re.IGNORECASE,
)
DISTANCE_KM = re.compile(r"(\d+(?:[.,]\d+)?)\s*km\b", re.IGNORECASE)
# Google's consent page button; English first, with the Belgian languages as fallbacks.
CONSENT_BUTTON = re.compile(r"^\s*(?:reject all|alles afwijzen|tout refuser|alle ablehnen)\s*$",
                            re.IGNORECASE)


@dataclass(frozen=True)
class Point:
    id: str
    name: str
    location: str


def directions_url(origin: str, destination: str) -> str:
    query = urlencode({"api": "1", "origin": origin, "destination": destination, "travelmode": "driving"})
    return f"https://www.google.com/maps/dir/?{query}"


def origins_from_args(values: list[str] | None, path: Path | None) -> list[str]:
    origins = [value.strip() for value in values or [] if value.strip()]
    if path:
        origins.extend(line.strip() for line in path.read_text(encoding="utf-8").splitlines()
                       if line.strip() and not line.lstrip().startswith("#"))
    return list(dict.fromkeys(origins))


def load_points(path: Path) -> list[Point]:
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames or not {"id", "name", "location"}.issubset(reader.fieldnames):
            raise ValueError("points file must have id,name,location columns")
        points = [Point(row["id"].strip(), row["name"].strip(), row["location"].strip())
                  for row in reader if row and any((value or "").strip() for value in row.values())]
    if len(points) < 2 or any(not point.id or not point.name or not point.location for point in points):
        raise ValueError("points file needs at least two complete rows")
    if len({point.id for point in points}) != len(points):
        raise ValueError("point IDs must be unique")
    if len({point.location for point in points}) != len(points):
        raise ValueError("point locations must be unique")
    return points


def route_pairs(args, parser: argparse.ArgumentParser) -> list[tuple[Point, Point]]:
    if args.points_file:
        if args.origin or args.origins_file or args.destination:
            parser.error("--points-file cannot be combined with --origin, --origins-file, or --destination")
        try:
            points = load_points(args.points_file)
        except (OSError, ValueError) as error:
            parser.error(str(error))
        return list(permutations(points, 2))
    origins = origins_from_args(args.origin, args.origins_file)
    if not origins or not args.destination:
        parser.error("provide --points-file, or an origin and --destination")
    destination = Point(args.destination, args.destination, args.destination)
    return [(Point(origin, origin, origin), destination) for origin in origins if origin != args.destination]


def default_storage_dirs(points_file: Path | None, batch_start: int, batch_size: int | None,
                         batch_order: str = "sequential", batch_seed: int = 42) -> tuple[Path, Path]:
    if (points_file is None or points_file.stem == "google_maps_points") and not batch_start and batch_size is None:
        return ROOT / "data" / "google_maps_captures", ROOT / "data" / "google_maps_profile"
    stem = points_file.stem if points_file else "manual"
    slug = re.sub(r"[^a-z0-9_-]+", "_", stem.lower()).strip("_") or "points"
    if batch_start or batch_size is not None:
        slug += f"_batch_{batch_start}_{batch_size if batch_size is not None else 'rest'}"
        if batch_order == "random":
            slug += f"_random_{batch_seed}"
    return ROOT / "data" / f"google_maps_{slug}_captures", ROOT / "data" / f"google_maps_{slug}_profile"


def visible_route_text(page) -> str | None:
    """Read the first visible route card, if Google exposes one in the page."""
    candidates = page.locator('[data-trip-index="0"]')
    for index in range(min(candidates.count(), 5)):
        card = candidates.nth(index)
        if card.is_visible():
            value = " ".join(re.sub(r"[\ue000-\uf8ff]", "", card.inner_text(timeout=3000)).split())
            if TRAVEL_TIME.search(value):
                return value
    return None


def duration_minutes(value: str) -> int:
    hours = re.search(r"(\d+)\s*(?:h|hr|hrs|hour|hours|u|uur)\b", value, re.IGNORECASE)
    minutes = re.search(r"(\d+)\s*(?:min|mins|minute|minutes)\b", value, re.IGNORECASE)
    return (int(hours.group(1)) * 60 if hours else 0) + (int(minutes.group(1)) if minutes else 0)


def dismiss_consent(page) -> bool:
    """Click "Reject all" on Google's consent page; the profile remembers the choice."""
    try:
        page.get_by_role("button", name=CONSENT_BUTTON).first.click(timeout=10000)
        page.wait_for_url(lambda url: "consent.google.com" not in url, timeout=15000)
    except PlaywrightError:
        return False
    return True


def wait_for_route(page, timeout_seconds: int, *, headed: bool) -> str | None:
    if "consent.google.com" in page.url and not dismiss_consent(page) and headed:
        print("Could not dismiss Google consent automatically. "
              "Choose an option in the browser window to continue.", flush=True)
        consent_deadline = time.monotonic() + 180
        while "consent.google.com" in page.url and time.monotonic() < consent_deadline:
            page.wait_for_timeout(1000)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            route = visible_route_text(page)
            if route:
                return route
        except PlaywrightError:
            pass
        page.wait_for_timeout(1000)
    return None


def add_capture_label(page, label: str) -> None:
    page.evaluate(
        """label => {
          document.getElementById('travelsmart-capture-label')?.remove();
          const badge = document.createElement('div');
          badge.id = 'travelsmart-capture-label';
          badge.textContent = label;
          Object.assign(badge.style, {
            position: 'fixed', right: '16px', bottom: '32px', zIndex: '2147483647',
            background: 'rgba(20, 24, 30, .94)', color: '#fff',
            font: '600 16px Arial, sans-serif', padding: '12px 16px',
            borderRadius: '8px', boxShadow: '0 2px 10px rgba(0,0,0,.35)',
            pointerEvents: 'none', whiteSpace: 'pre-wrap', maxWidth: '60vw'
          });
          document.body.appendChild(badge);
        }""",
        label,
    )


def fresh_page(context, page):
    """Swap in a new tab before each route. With some profiles Chromium keeps one renderer process
    alive per Google Maps visit in a reused tab (about 100 MB each), until the machine runs out of
    memory; closing the tab releases them."""
    fresh = context.new_page()
    page.close()
    return fresh


def capture(page, *, origin: Point, destination: Point, output_dir: Path,
            timeout_seconds: int, headed: bool, settle_seconds: float,
            screenshots: bool, sweep_id: str, pair_index: int, pair_count: int,
            metadata: dict | None = None) -> dict:
    url = directions_url(origin.location, destination.location)
    page.goto("about:blank", wait_until="load")
    page.goto(url, wait_until="domcontentloaded", timeout=timeout_seconds * 1000)
    route_text = wait_for_route(page, timeout_seconds, headed=headed)
    if route_text and settle_seconds:
        page.wait_for_timeout(settle_seconds * 1000)
        route_text = visible_route_text(page) or route_text
    captured_at = datetime.now(LOCAL_TIMEZONE)
    output_dir.mkdir(parents=True, exist_ok=True)
    travel_time = TRAVEL_TIME.search(route_text).group(0) if route_text else None
    distance = DISTANCE_KM.search(route_text) if route_text else None
    record = {
        "captured_at": captured_at.isoformat(),
        "sweep_id": sweep_id,
        "pair_index": pair_index,
        "pair_count": pair_count,
        "origin": origin.name,
        "destination": destination.name,
        "origin_id": origin.id,
        "destination_id": destination.id,
        "origin_location": origin.location,
        "destination_location": destination.location,
        "travel_time_text": travel_time,
        "travel_time_minutes": duration_minutes(travel_time) if travel_time else None,
        "distance_km": float(distance.group(1).replace(",", ".")) if distance else None,
        "route_card_text": route_text,
        "status": "ok" if route_text else ("consent_required" if "consent.google.com" in page.url
                                             else "route_not_detected"),
        "url": page.url,
    }
    if metadata:
        record.update(metadata)
    if screenshots:
        stamp = captured_at.strftime("%Y-%m-%d_%H-%M-%S-%f")
        screenshot = output_dir / f"google_maps_{stamp}.png"
        label = f"{captured_at:%Y-%m-%d %H:%M:%S} Europe/Brussels\n{origin.name} → {destination.name}"
        add_capture_label(page, label)
        page.screenshot(path=str(screenshot), animations="disabled", timeout=30000)
        record["screenshot"] = str(screenshot.resolve())
    with (output_dir / "captures.jsonl").open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", action="append", help="Starting address or place; repeat for several")
    parser.add_argument("--origins-file", type=Path, help="UTF-8 file with one starting place per line")
    parser.add_argument("--destination", help="Destination address or place")
    parser.add_argument("--points-file", type=Path, help="CSV list; collect every ordered pair of points")
    parser.add_argument("--batch-start", type=int, default=0,
                        help="Zero-based first pair in the batch order (default: 0)")
    parser.add_argument("--batch-size", type=int,
                        help="Maximum pairs in this batch; use with --once for large lists")
    parser.add_argument("--batch-order", choices=("random", "sequential"), default="random",
                        help="How to assign pairs to batches (default: random)")
    parser.add_argument("--batch-seed", type=int, default=42,
                        help="Fixed seed for random batch assignment; use the same seed for all batches (default: 42)")
    parser.add_argument("--interval-minutes", type=float, default=5,
                        help="Minimum time between starts of sweeps (default: 5)")
    parser.add_argument("--delay-seconds", type=float, default=1,
                        help="Minimum pause between routes (default: 1)")
    parser.add_argument("--jitter-seconds", type=float, default=0,
                        help="Additional random pause between routes (default: 0)")
    parser.add_argument("--settle-seconds", type=float, default=2,
                        help="Pause after a route appears before reading it (default: 2)")
    parser.add_argument("--once", action="store_true", help="Run one full sweep and exit")
    parser.add_argument("--dry-run", action="store_true", help="Show route count without opening a browser")
    parser.add_argument("--screenshots", action="store_true", help="Also save labeled PNGs")
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True,
                        help="Hide the browser window (default); --no-headless shows it")
    parser.add_argument("--timeout-seconds", type=int, default=45, help="Wait for route to appear (default: 45)")
    parser.add_argument("--output-dir", type=Path, help="Observation folder; defaults to one per point list")
    parser.add_argument("--profile-dir", type=Path, help="Browser profile; defaults to one per point list")
    args = parser.parse_args()
    default_output, default_profile = default_storage_dirs(
        args.points_file, args.batch_start, args.batch_size, args.batch_order, args.batch_seed)
    args.output_dir = args.output_dir or default_output
    args.profile_dir = args.profile_dir or default_profile
    pairs = route_pairs(args, parser)
    total_pairs = len(pairs)
    point_count = len({point.id for pair in pairs for point in pair})
    if args.batch_start < 0 or (args.batch_size is not None and args.batch_size <= 0):
        parser.error("batch start must be nonnegative and batch size must be positive")
    indexed_pairs = list(enumerate(pairs, start=1))
    if args.batch_order == "random" and (args.batch_start or args.batch_size is not None):
        random.Random(args.batch_seed).shuffle(indexed_pairs)
    if args.batch_start or args.batch_size is not None:
        indexed_pairs = indexed_pairs[args.batch_start:args.batch_start + args.batch_size
                                      if args.batch_size else None]
    if not indexed_pairs:
        parser.error("no routes to collect")
    if (args.interval_minutes <= 0 or args.timeout_seconds <= 0 or args.delay_seconds < 0
            or args.jitter_seconds < 0 or args.settle_seconds < 0):
        parser.error("interval and timeout must be positive; delays cannot be negative")
    if args.output_dir.resolve() == args.profile_dir.resolve():
        parser.error("output and profile directories must differ")
    if args.dry_run:
        print(json.dumps({"points": point_count, "total_directed_routes": total_pairs,
                          "directed_routes": len(indexed_pairs),
                          "batch_start": args.batch_start,
                          "batch_order": args.batch_order,
                          "batch_seed": args.batch_seed if args.batch_order == "random" else None,
                          "output_dir": str(args.output_dir.resolve()),
                          "profile_dir": str(args.profile_dir.resolve()),
                          "minimum_pause_minutes": round((len(indexed_pairs) - 1) * args.delay_seconds / 60, 1)}))
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
                      "Let that collector finish, or run with --profile-dir pointing to a different folder.",
                      file=sys.stderr)
            else:
                print(f"Could not start Chromium: {error}", file=sys.stderr)
            return 2
        page = context.pages[0] if context.pages else context.new_page()
        try:
            while True:
                started = time.monotonic()
                sweep_id = datetime.now(LOCAL_TIMEZONE).isoformat()
                failed = False
                cycle_pairs = indexed_pairs.copy()
                random.shuffle(cycle_pairs)
                for index, (global_pair_index, (origin, destination)) in enumerate(cycle_pairs):
                    if index:
                        time.sleep(args.delay_seconds + random.uniform(0, args.jitter_seconds))
                    try:
                        page = fresh_page(context, page)
                        record = capture(page, origin=origin, destination=destination,
                                         output_dir=args.output_dir, timeout_seconds=args.timeout_seconds,
                                         headed=not args.headless, settle_seconds=args.settle_seconds,
                                         screenshots=args.screenshots, sweep_id=sweep_id,
                                         pair_index=index + 1, pair_count=len(cycle_pairs),
                                         metadata={"global_pair_index": global_pair_index,
                                                   "batch_order": args.batch_order,
                                                   "batch_seed": args.batch_seed if args.batch_order == "random" else None}
                                         if args.batch_size is not None or args.batch_start else None)
                        print(json.dumps(record, ensure_ascii=True), flush=True)
                        failed |= record["status"] != "ok"
                    except PlaywrightError as error:
                        failed = True
                        print(f"Capture failed for {origin.name} → {destination.name}: {error}",
                              file=sys.stderr, flush=True)
                if args.once:
                    return 1 if failed else 0
                next_cycle_pause = max(args.delay_seconds + random.uniform(0, args.jitter_seconds),
                                       args.interval_minutes * 60 - (time.monotonic() - started))
                time.sleep(next_cycle_pause)
        except KeyboardInterrupt:
            return 0
        finally:
            context.close()


if __name__ == "__main__":
    raise SystemExit(main())
