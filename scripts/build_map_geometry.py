"""Road paths for the "When is it calm?" map, from OpenStreetMap routing (OSRM).

For every start town and every place on the map at least 12 km away, fetch the driving
route once (simplified overview, encoded polyline) and store it in
config/commute_geometry.json. A -> B and B -> A share one path. Already stored pairs
are kept, so the script can be re-run after the catalogue changes. No TomTom/HERE calls.
"""

from __future__ import annotations

import csv
import json
import time
from math import asin, cos, radians, sin, sqrt

import httpx
import yaml

from travelsmart.commute_export import build_map
from travelsmart.config import ROOT

OSRM = "https://router.project-osrm.org"
USER_AGENT = {"User-Agent": "TravelSmart-map-geometry/0.1 (github.com/kvandebeek/travelsmart)"}
OUTPUT = ROOT / "config" / "commute_geometry.json"


def km(a, b) -> float:
    lat1, lon1, lat2, lon2 = map(radians, (*a, *b))
    return 12742 * asin(sqrt(sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2))


def pair_key(a: str, b: str) -> str:
    return "|".join(sorted((a, b)))


def main() -> None:
    config = ROOT / "config"
    with (config / "commute_routes.csv").open(encoding="utf-8", newline="") as file:
        routes = list(csv.DictReader(file))
    catalogue = yaml.safe_load((config / "commute_catalogue.yaml").read_text(encoding="utf-8"))
    data = build_map(routes, catalogue, [], config, "")
    places = data["places"]
    paths = json.loads(OUTPUT.read_text(encoding="utf-8")) if OUTPUT.exists() else {}
    wanted = sorted({pair_key(origin, other) for origin in data["origins"] for other in places
                     if other != origin and km(places[origin]["point"], places[other]["point"]) >= 12})
    todo = [key for key in wanted if key not in paths]
    print(f"{len(wanted)} town pairs, {len(todo)} to fetch", flush=True)
    client = httpx.Client(headers=USER_AGENT, timeout=60)
    for index, key in enumerate(todo, 1):
        a, b = key.split("|")
        (lat1, lon1), (lat2, lon2) = places[a]["point"], places[b]["point"]
        for attempt in range(3):
            try:
                reply = client.get(f"{OSRM}/route/v1/driving/{lon1},{lat1};{lon2},{lat2}",
                                   params={"overview": "simplified", "geometries": "polyline"}).json()
                break
            except (httpx.HTTPError, ValueError):
                time.sleep(5 * (attempt + 1))
        else:
            continue
        if reply.get("code") == "Ok":
            paths[key] = reply["routes"][0]["geometry"]  # encoded polyline, from a to b (sorted order)
        if index % 50 == 0 or index == len(todo):
            OUTPUT.write_text(json.dumps(dict(sorted(paths.items())), separators=(",", ":")), encoding="utf-8")
            print(f"{index}/{len(todo)}", flush=True)
        time.sleep(1.0)  # public OSRM demo server: about one request per second
    OUTPUT.write_text(json.dumps(dict(sorted(paths.items())), separators=(",", ":")), encoding="utf-8")
    print(f"stored {len(paths)} paths, {OUTPUT.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
