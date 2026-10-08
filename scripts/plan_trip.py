"""When to leave for a trip: the week's heatmap of total travel time (docs/network-design.md §10).

Takes two places, snaps each to the network, and solves the fastest route for all 48 half-hour
departures of every weekday. The answer is a typical-week profile, not a live travel time, so it
says which half hours are normally good and which are not.

Half hours whose total is within measurement noise of the best are marked as equally good, because
"leave at 06:30 exactly" is a false precision when 06:30 and 07:00 differ by less than the spread.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from travelsmart.measure.profiles import Cell, Profile
from travelsmart.measure.routing import fastest_route, minute_of_week

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
BLOCKS = " ▁▂▃▄▅▆▇█"


def load_profiles(path: Path) -> dict[str, Profile]:
    """The published profiles, back as Profile objects the router can read."""
    if not path.exists():
        return {}
    published = json.loads(path.read_text(encoding="utf-8"))["profiles"]
    profiles = {}
    for edge_id, entry in published.items():
        cells = {}
        for key, cell in entry.get("cells", {}).items():
            weekday, half_hour = (int(part) for part in key.split(":"))
            cells[(weekday, half_hour)] = Cell(cell["count"], cell["median"], cell["spread"])
        profile = Profile(edge_id=edge_id, provider="combined", cells=cells,
                          usual=entry.get("usual", 0.0))
        profiles[edge_id] = profile
    return profiles


def nearest_node(nodes: dict[str, dict], lat: float, lon: float) -> str:
    return min(nodes, key=lambda node_id: (nodes[node_id]["lat"] - lat) ** 2
               + (nodes[node_id]["lon"] - lon) ** 2)


def parse_place(value: str, nodes: dict[str, dict]) -> str:
    """A node id, or a "lat,lon" snapped to the nearest node."""
    if value in nodes:
        return value
    try:
        lat, lon = (float(part) for part in value.split(","))
    except ValueError:
        raise SystemExit(f"{value!r} is neither a node id nor a lat,lon")
    return nearest_node(nodes, lat, lon)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("origin", help="node id, or lat,lon")
    parser.add_argument("destination", help="node id, or lat,lon")
    parser.add_argument("--network", type=Path, default=Path("data/network/network.json"))
    parser.add_argument("--profiles", type=Path, default=Path("data/network/profiles.json"))
    parser.add_argument("--days", type=int, default=5, help="how many weekdays to show")
    args = parser.parse_args()

    network = json.loads(args.network.read_text(encoding="utf-8"))
    nodes, edges = network["nodes"], network["edges"]
    profiles = load_profiles(args.profiles)
    start, end = parse_place(args.origin, nodes), parse_place(args.destination, nodes)
    if start == end:
        raise SystemExit("origin and destination snap to the same network node")

    print(f"{start} -> {end} over {len(profiles):,} measured edges", file=sys.stderr)
    grid: dict[int, list[float | None]] = {}
    measured = []
    for weekday in range(args.days):
        row = []
        for half_hour in range(48):
            route = fastest_route(edges, profiles, start, end, minute_of_week(weekday, half_hour * 30))
            row.append(route.seconds if route else None)
            if route:
                measured.append(route.measured_share)
        grid[weekday] = row
    times = [value for row in grid.values() for value in row if value is not None]
    if not times:
        raise SystemExit("no route between these points")
    quickest, slowest = min(times), max(times)

    print(f"\nfastest {quickest / 60:.0f} min, slowest {slowest / 60:.0f} min, "
          f"{sum(measured) / len(measured):.0%} of the route is measured\n")
    print(" " * 10 + "".join(f"{hour:<2}" for hour in range(24)))
    for weekday, row in grid.items():
        bar = "".join(_block(value, quickest, slowest) for value in row)
        best = min((value for value in row if value is not None), default=None)
        print(f"{DAYS[weekday]:<10}{bar}  best {best / 60:.0f} min" if best else f"{DAYS[weekday]:<10}{bar}")
    print("\n" + _best_windows(grid, quickest))


def _block(value: float | None, quickest: float, slowest: float) -> str:
    if value is None:
        return "?"
    if slowest <= quickest:
        return BLOCKS[0]
    return BLOCKS[min(len(BLOCKS) - 1, int((value - quickest) / (slowest - quickest) * (len(BLOCKS) - 1)))]


def _best_windows(grid: dict[int, list[float | None]], quickest: float, tolerance: float = 0.05) -> str:
    """Every half hour within a few per cent of the very best, which count as equally good."""
    lines = []
    for weekday, row in grid.items():
        good = [half for half, value in enumerate(row)
                if value is not None and value <= quickest * (1 + tolerance)]
        if good:
            spans = []
            for half in good:
                if spans and half == spans[-1][1] + 1:
                    spans[-1][1] = half
                else:
                    spans.append([half, half])
            readable = ", ".join(f"{_clock(a)}-{_clock(b + 1)}" for a, b in spans)
            lines.append(f"{DAYS[weekday]:<10}{readable}")
    return "equally good departures (within 5% of the best):\n" + ("\n".join(lines) or "  none")


def _clock(half_hour: int) -> str:
    """A half-hour index as a clock time. 48 is the end of the day, which reads 24:00, not 00:00."""
    hour = half_hour // 2
    return f"{hour if hour == 24 else hour % 24:02d}:{'30' if half_hour % 2 else '00'}"


if __name__ == "__main__":
    main()
