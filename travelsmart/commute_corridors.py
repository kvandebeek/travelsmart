"""Motorway corridors: one TomTom route with intermediate stops, travel time per leg.

Corridor points are placed on the motorway itself, on the carriageway of the direction of
travel: an OpenStreetMap (OSRM) route along the corridor is reduced to its motorway steps,
and each place gets the nearest point on those steps (in order along the route). The first
and last points are the map towns themselves.
"""

from __future__ import annotations

import csv
import json
import time
from datetime import datetime, timezone
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

import httpx
import yaml

from travelsmart.config import ROOT

OSRM = "https://router.project-osrm.org"
USER_AGENT = {"User-Agent": "TravelSmart-corridors/0.1 (github.com/kvandebeek/travelsmart)"}
NODES_FILE = "commute_corridor_nodes.json"
# A leg more than this much longer than the OpenStreetMap leg means TomTom snapped a point to the
# wrong carriageway (a U-turn at the next exit): that leg is not usable.
MAX_LEG_STRETCH = 1.35


def km(a, b) -> float:
    lat1, lon1, lat2, lon2 = map(radians, (*a, *b))
    return 12742 * asin(sqrt(sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2))


def _town_points(config_dir: Path) -> dict[str, list[float]]:
    from travelsmart.commute_export import build_map
    with (config_dir / "commute_routes.csv").open(encoding="utf-8", newline="") as file:
        routes = list(csv.DictReader(file))
    catalogue = yaml.safe_load((config_dir / "commute_catalogue.yaml").read_text(encoding="utf-8"))
    return {key: value["point"] for key, value in build_map(routes, catalogue, [], config_dir, "")["places"].items()}


def _geocode(client: httpx.Client, query: str) -> list[float]:
    time.sleep(1.1)  # Nominatim: at most one request per second
    rows = client.get("https://nominatim.openstreetmap.org/search",
                      params={"q": query, "format": "jsonv2", "countrycodes": "be", "limit": 1}).json()
    return [float(rows[0]["lat"]), float(rows[0]["lon"])]


def _motorway_track(client: httpx.Client, guide: list[list[float]], motorways: list[str]) -> list[list[float]]:
    """Points [lat, lon] of the motorway steps of an OSRM route through the guide points, in order."""
    coordinates = ";".join(f"{lon},{lat}" for lat, lon in guide)
    reply = client.get(f"{OSRM}/route/v1/driving/{coordinates}",
                       params={"steps": "true", "geometries": "geojson", "overview": "false"}).json()
    if reply.get("code") != "Ok":
        raise RuntimeError(f"OSRM route failed: {reply.get('message', reply.get('code'))}")
    track = []
    for leg in reply["routes"][0]["legs"]:
        for step in leg["steps"]:
            refs = {part.strip().replace(" ", "").replace(" ", "") for part in (step.get("ref") or "").split(";")}
            if refs & {m.replace(" ", "") for m in motorways}:   # OSM writes "E40", config may say "E 40"
                track.extend([lat, lon] for lon, lat in step["geometry"]["coordinates"])
    if not track:
        raise RuntimeError("No motorway steps found on the guide route")
    return track


def _expected_legs(client: httpx.Client, points: list[list[float]]) -> list[float]:
    coordinates = ";".join(f"{lon},{lat}" for lat, lon in points)
    reply = client.get(f"{OSRM}/route/v1/driving/{coordinates}", params={"overview": "false"}).json()
    return [round(leg["distance"]) for leg in reply["routes"][0]["legs"]]


def build_nodes(config_dir: Path = ROOT / "config") -> dict:
    """Place every corridor point on the motorway, per direction, and store them."""
    spec = yaml.safe_load((config_dir / "commute_corridors.yaml").read_text(encoding="utf-8"))
    towns = _town_points(config_dir)
    client = httpx.Client(headers=USER_AGENT, timeout=60)
    places = {}
    for corridor in spec["corridors"]:
        for node in corridor["nodes"]:
            if node["id"] not in places:
                places[node["id"]] = towns[node["town"]] if "town" in node else _geocode(client, node["query"])
    result = {}
    for corridor in spec["corridors"]:
        result[corridor["id"]] = {"name": corridor["name"]}
        for direction in ("forward", "reverse"):
            nodes = corridor["nodes"] if direction == "forward" else corridor["nodes"][::-1]
            guide = corridor["guide"] if direction == "forward" else corridor["guide"][::-1]
            track = _motorway_track(client, [towns[key] for key in guide], corridor["motorways"])
            points, cursor = [], 0
            for index, node in enumerate(nodes):
                if index in (0, len(nodes) - 1):        # end points: the town itself
                    points.append(places[node["id"]])
                    continue
                best = min(range(cursor, len(track)), key=lambda i: km(track[i], places[node["id"]]))
                cursor = best
                points.append([round(track[best][0], 6), round(track[best][1], 6)])
            result[corridor["id"]][direction] = {
                "nodes": [{"id": n["id"], "name": n["name"], "point": p} for n, p in zip(nodes, points)],
                "expected_leg_m": _expected_legs(client, points)}
            time.sleep(1)
    (config_dir / NODES_FILE).write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    return result


def fetch_corridor(nodes: list[dict], key: str) -> list[dict]:
    """One TomTom request through all corridor points: travel time per leg, at live traffic."""
    locations = ":".join(f"{n['point'][0]:.6f},{n['point'][1]:.6f}" for n in nodes)
    response = httpx.get(f"https://api.tomtom.com/routing/1/calculateRoute/{locations}/json",
                         params={"key": key, "traffic": "true", "departAt": "now", "travelMode": "car",
                                 "computeTravelTimeFor": "all"}, timeout=30)
    response.raise_for_status()
    legs = response.json()["routes"][0]["legs"]
    return [{"from": a["id"], "to": b["id"], "distance_m": leg["summary"]["lengthInMeters"],
             "duration_seconds": leg["summary"]["travelTimeInSeconds"],
             "freeflow_seconds": leg["summary"].get("noTrafficTravelTimeInSeconds"),
             "traffic_delay_seconds": leg["summary"].get("trafficDelayInSeconds")}
            for a, b, leg in zip(nodes, nodes[1:], legs)]


def measure(corridor_id: str, direction: str, key: str, account: str, scheduled_at: str | None = None,
            config_dir: Path = ROOT / "config", data_dir: Path = ROOT / "observations") -> dict:
    """Measure one corridor in one direction and append the result to the month's JSONL file."""
    corridor = json.loads((config_dir / NODES_FILE).read_text(encoding="utf-8"))[corridor_id][direction]
    observed = datetime.now(timezone.utc)
    row = {"provider": "tomtom", "account": account, "corridor": corridor_id, "direction": direction,
           "scheduled_at": scheduled_at, "observed_at": observed.isoformat(), "status": "error"}
    try:
        legs = fetch_corridor(corridor["nodes"], key)
        for leg, expected in zip(legs, corridor["expected_leg_m"]):
            leg["usable"] = leg["distance_m"] <= expected * MAX_LEG_STRETCH + 500
        row.update({"status": "ok", "legs": legs})
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as error:
        row["error_type"] = type(error).__name__  # never the message: request URLs contain keys
    path = data_dir / "corridors" / f"{observed:%Y-%m}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as file:
        file.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    return row
