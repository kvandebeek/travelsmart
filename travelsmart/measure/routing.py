"""Time-dependent fastest path over the measured network (docs/network-design.md §9).

A trip leaving at time *t* drives its first edge at *t*, the next at *t* plus the first edge's time,
and so on, so an edge's cost depends on when the route reaches it. That is the whole point: leaving
ten minutes later can put you in a different jam.

An edge's time at a moment is interpolated between its two nearest half-hour cells, so the answer
moves smoothly across a half hour boundary instead of jumping. An edge nothing has measured yet
falls back on free flow from its length, which keeps a route computable long before the whole
network is covered; `measured_share` says how much of a route actually rests on measurements.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field

from travelsmart.measure.profiles import HALF_HOURS, WEEKDAYS, Profile, lookup

HALF_HOUR_SECONDS = 1800
# Free flow where nothing has been measured, in km/h, by edge kind. Deliberately plain: this is a
# placeholder that a measurement replaces, not a model worth tuning.
FREE_FLOW_KMH = {"motorway": 110, "transfer": 80, "regional": 60,
                 "ramp_on": 60, "ramp_off": 60, "ramp_link": 50, "access": 40, "junction": 30}
DEFAULT_KMH = 50


def free_flow_seconds(edge: dict) -> float:
    kmh = FREE_FLOW_KMH.get(edge.get("kind", ""), DEFAULT_KMH)
    return edge.get("metres", 0) / (kmh / 3.6) if kmh else 0.0


def minute_of_week(weekday: int, minutes: int) -> int:
    return (weekday % WEEKDAYS) * 24 * 60 + minutes % (24 * 60)


def edge_seconds(edge: dict, profile: Profile | None, minute: int) -> tuple[float, bool]:
    """How long this edge takes for a trip reaching it at this minute of the week.

    Returns the seconds and whether they came from measurements. The time is interpolated between
    the two half hours the moment falls between, so crossing 08:29 to 08:30 is gradual.
    """
    if profile is None or not profile.cells:
        return free_flow_seconds(edge), False
    minute %= WEEKDAYS * 24 * 60
    weekday, minute_of_day = divmod(minute, 24 * 60)
    # A cell is labelled by the start of its half hour, so 08:00 is cell 16 exactly and 08:15 sits
    # halfway between cells 16 and 17.
    position = minute_of_day / 30
    first = int(position // 1)
    share = position - first
    before = _cell_seconds(profile, weekday, first)
    after = _cell_seconds(profile, weekday, first + 1)
    if before is None and after is None:
        return free_flow_seconds(edge), False
    if before is None:
        return after, True
    if after is None:
        return before, True
    return before + (after - before) * share, True


def _cell_seconds(profile: Profile, weekday: int, half_hour: int) -> float | None:
    """One half hour's time, rolling into the day before or after at the edges of the day."""
    day, half = divmod(half_hour, HALF_HOURS)
    return lookup(profile, (weekday + day) % WEEKDAYS, half)


@dataclass
class Route:
    seconds: float = 0.0
    edges: list[str] = field(default_factory=list)
    nodes: list[str] = field(default_factory=list)
    measured_edges: int = 0

    @property
    def measured_share(self) -> float:
        return self.measured_edges / len(self.edges) if self.edges else 0.0


def fastest_route(edges: dict[str, dict], profiles: dict[str, Profile], start: str, end: str,
                  departure_minute: int, limit_seconds: float = 6 * 3600) -> Route | None:
    """The quickest way from start to end for a trip leaving at this minute of the week.

    Edges are explored in the order a driver would reach them, so each one is costed for the moment
    the route actually arrives there rather than for the departure time.
    """
    if start == end:
        return Route()
    out_edges: dict[str, list[tuple[str, dict]]] = {}
    for edge_id, edge in edges.items():
        out_edges.setdefault(edge["from"], []).append((edge_id, edge))

    best: dict[str, float] = {start: 0.0}
    previous: dict[str, tuple[str, str, bool]] = {}
    queue: list[tuple[float, str]] = [(0.0, start)]
    while queue:
        elapsed, node = heapq.heappop(queue)
        if elapsed > best.get(node, float("inf")) or elapsed > limit_seconds:
            continue
        if node == end:
            return _rebuild(previous, start, end, elapsed)
        minute = departure_minute + int(elapsed // 60)
        for edge_id, edge in out_edges.get(node, ()):
            seconds, measured = edge_seconds(edge, profiles.get(edge_id), minute)
            arrival = elapsed + seconds
            if arrival < best.get(edge["to"], float("inf")):
                best[edge["to"]] = arrival
                previous[edge["to"]] = (node, edge_id, measured)
                heapq.heappush(queue, (arrival, edge["to"]))
    return None


def _rebuild(previous, start: str, end: str, seconds: float) -> Route:
    route = Route(seconds=seconds)
    node = end
    while node != start:
        node, edge_id, measured = previous[node]
        route.edges.append(edge_id)
        route.nodes.append(node)
        route.measured_edges += measured
    route.edges.reverse()
    route.nodes.reverse()
    route.nodes.append(end)
    return route


def departure_sweep(edges: dict[str, dict], profiles: dict[str, Profile], start: str, end: str,
                    weekday: int) -> list[tuple[int, float | None]]:
    """The trip's total time for all 48 half-hour departures of one weekday (§10's heatmap row)."""
    sweep = []
    for half_hour in range(HALF_HOURS):
        route = fastest_route(edges, profiles, start, end,
                              minute_of_week(weekday, half_hour * 30))
        sweep.append((half_hour, route.seconds if route else None))
    return sweep
