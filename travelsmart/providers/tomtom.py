"""TomTom Calculate Route: one request measures a whole chain (docs/network-design.md §6.1).

The route is asked for as a colon-separated list of the chain's waypoints. The answer carries a
summary per leg, so an n-waypoint request returns n-1 measured legs. `computeTravelTimeFor=all` adds
the free-flow and TomTom's own historic time next to the live one, which is what §8 calibrates
against.

The key lives in the environment, never in the repository. Two accounts share the monthly free tier,
so `api_keys()` returns both and the caller rotates.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime

import httpx

from travelsmart.verified_fetch import USER_AGENT, verified_context

ROUTING_URL = "https://api.tomtom.com/routing/1/calculateRoute/{locations}/json"
MAX_WAYPOINTS = 150


@dataclass(frozen=True)
class Leg:
    """One measured stretch between two consecutive waypoints."""
    index: int
    metres: float
    seconds: float
    free_flow_seconds: float | None
    typical_seconds: float | None
    traffic_delay_seconds: float | None
    departure: datetime | None
    arrival: datetime | None


def api_keys() -> list[str]:
    """Every TomTom key configured, in order. Raises when none is set."""
    keys = [os.environ[name] for name in ("TOMTOM_API_KEY", "TOMTOM_API_KEY2") if os.environ.get(name)]
    if not keys:
        raise RuntimeError("no TomTom key: set TOMTOM_API_KEY in the environment")
    return keys


def _moment(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def parse_legs(payload: dict) -> list[Leg]:
    """The per-leg summaries of the first route in a Calculate Route answer."""
    routes = payload.get("routes") or []
    if not routes:
        return []
    legs = []
    for index, leg in enumerate(routes[0].get("legs") or []):
        summary = leg.get("summary") or {}
        legs.append(Leg(
            index=index,
            metres=float(summary.get("lengthInMeters", 0)),
            seconds=float(summary.get("travelTimeInSeconds", 0)),
            free_flow_seconds=_optional(summary, "noTrafficTravelTimeInSeconds"),
            typical_seconds=_optional(summary, "historicTrafficTravelTimeInSeconds"),
            traffic_delay_seconds=_optional(summary, "trafficDelayInSeconds"),
            departure=_moment(summary.get("departureTime")),
            arrival=_moment(summary.get("arrivalTime")),
        ))
    return legs


def _optional(summary: dict, key: str) -> float | None:
    return float(summary[key]) if summary.get(key) is not None else None


def measure_chain(waypoints: list[tuple[float, float]], key: str, *, timeout: float = 60,
                  client: httpx.Client | None = None) -> list[Leg]:
    """Measure every leg between these waypoints in one request.

    Waypoints are (lat, lon) in driving order, at most MAX_WAYPOINTS of them.
    """
    if not 2 <= len(waypoints) <= MAX_WAYPOINTS:
        raise ValueError(f"a route needs 2 to {MAX_WAYPOINTS} waypoints, got {len(waypoints)}")
    locations = ":".join(f"{lat:.6f},{lon:.6f}" for lat, lon in waypoints)
    params = {"key": key, "routeType": "fastest", "traffic": "true", "travelMode": "car",
              "computeTravelTimeFor": "all"}
    owned = client is None
    client = client or httpx.Client(verify=verified_context(), timeout=timeout,
                                    headers={"User-Agent": USER_AGENT})
    try:
        response = client.get(ROUTING_URL.format(locations=locations), params=params)
        response.raise_for_status()
        return parse_legs(response.json())
    finally:
        if owned:
            client.close()
