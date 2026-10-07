from __future__ import annotations
from dataclasses import dataclass
import os
import httpx
from travelsmart.config import Location

@dataclass(frozen=True)
class RouteResult:
    distance_m: float
    duration_seconds: float
    geometry: str | None = None

class OSRMProvider:
    """OSRM HTTP adapter. The public default is development-only; self-host in production."""
    name = "osrm"
    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or os.getenv("TRAVELSMART_OSRM_URL", "https://router.project-osrm.org")).rstrip("/")
    def calculate_route(self, origin: Location, destination: Location, departure_time=None, via=()) -> RouteResult:
        points = [origin, *via, destination]
        coords = ";".join(f"{p.longitude},{p.latitude}" for p in points)
        response = httpx.get(f"{self.base_url}/route/v1/driving/{coords}", params={"overview": "false"}, timeout=30)
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != "Ok" or not payload.get("routes"):
            raise RuntimeError(f"OSRM did not return a route: {payload.get('message', payload.get('code'))}")
        route = payload["routes"][0]
        return RouteResult(float(route["distance"]), float(route["duration"]), route.get("geometry"))
