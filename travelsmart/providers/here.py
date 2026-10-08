"""HERE Routing v8: a third opinion on a rotating sample of chains (docs/network-design.md §6.3).

Each stopover `via` starts its own section, and every section reports `duration` (with traffic),
`baseDuration` (free flow) and, on request, `typicalDuration`. An n-waypoint route therefore returns
n-1 measured sections, the same shape as a TomTom chain.

The cap is small: 96 requests a day and 3,000 a month, so this measures a rotating sample rather than
the whole backbone, and exists mainly to show whether the other two providers drift.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

from travelsmart.verified_fetch import USER_AGENT, verified_context

ROUTING_URL = "https://router.hereapi.com/v8/routes"
# HERE recommends at most about 100 vias, so a route covers at most that many sections plus one.
MAX_WAYPOINTS = 100
DAILY_LIMIT = 96


@dataclass(frozen=True)
class Section:
    """One measured stretch between two consecutive waypoints."""
    index: int
    metres: float
    seconds: float
    free_flow_seconds: float | None
    typical_seconds: float | None


def api_key() -> str:
    key = os.environ.get("HERE_api_key") or os.environ.get("HERE_API_KEY")
    if not key:
        raise RuntimeError("no HERE key: set HERE_api_key in the environment")
    return key


def parse_sections(payload: dict) -> list[Section]:
    """The per-section summaries of the first route in a Routing v8 answer."""
    routes = payload.get("routes") or []
    if not routes:
        return []
    sections = []
    for index, section in enumerate(routes[0].get("sections") or []):
        summary = section.get("summary") or {}
        sections.append(Section(
            index=index,
            metres=float(summary.get("length", 0)),
            seconds=float(summary.get("duration", 0)),
            free_flow_seconds=_optional(summary, "baseDuration"),
            typical_seconds=_optional(summary, "typicalDuration"),
        ))
    return sections


def _optional(summary: dict, key: str) -> float | None:
    return float(summary[key]) if summary.get(key) is not None else None


def measure_chain(waypoints: list[tuple[float, float]], key: str, *, timeout: float = 60,
                  client: httpx.Client | None = None) -> list[Section]:
    """Measure every section between these waypoints in one request."""
    if not 2 <= len(waypoints) <= MAX_WAYPOINTS:
        raise ValueError(f"a route needs 2 to {MAX_WAYPOINTS} waypoints, got {len(waypoints)}")
    origin, *middle, destination = waypoints
    params = [("origin", f"{origin[0]:.6f},{origin[1]:.6f}"),
              ("destination", f"{destination[0]:.6f},{destination[1]:.6f}"),
              ("transportMode", "car"), ("return", "summary,typicalDuration"), ("apiKey", key)]
    # A plain via is a stopover, which is what starts a new section for each one.
    params += [("via", f"{lat:.6f},{lon:.6f}") for lat, lon in middle]
    owned = client is None
    client = client or httpx.Client(verify=verified_context(), timeout=timeout,
                                    headers={"User-Agent": USER_AGENT})
    try:
        response = client.get(ROUTING_URL, params=params)
        response.raise_for_status()
        return parse_sections(response.json())
    finally:
        if owned:
            client.close()
