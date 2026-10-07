"""Build the commute catalogue: snapped home/work points, route pairs and road distances.

Inputs:  config/commute_catalogue.yaml (towns, employment areas, pairing rules)
         config/commute_homes.yaml     (residential start places, scripts/select_home_places.py)
Outputs: config/commute_anchors.yaml   (verified road-snapped points + road distance per route)
         config/commute_routes.csv     (the routes the planner may schedule)

Uses OpenStreetMap Nominatim (only for employment areas given as a `query`) and the public
OSRM demo server (`nearest` and `table`). Points already in commute_anchors.yaml are reused;
pass --refresh to look everything up again. No TomTom/HERE calls are made.
"""

from __future__ import annotations

import argparse
import csv
import time
from collections import Counter
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

import httpx
import yaml

from travelsmart.config import ROOT

OSRM = "https://router.project-osrm.org"
USER_AGENT = {"User-Agent": "TravelSmart-commute-catalogue/0.2 (github.com/kvandebeek/travelsmart)"}
TABLE_CHUNK = 40  # sources per OSRM table request, keeping each request under ~100 coordinates


def distance_km(a, b) -> float:
    lat1, lon1, lat2, lon2 = map(radians, (*a, *b))
    return 12742.0176 * asin(sqrt(sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2))


def geocode(client: httpx.Client, query: str, near: tuple[float, float]) -> tuple[list[float], str]:
    time.sleep(1.1)  # Nominatim usage policy: at most one request per second
    rows = client.get("https://nominatim.openstreetmap.org/search",
                      params={"q": query, "format": "jsonv2", "countrycodes": "be", "limit": 5}).json()
    for row in rows:
        point = [float(row["lat"]), float(row["lon"])]
        if distance_km(point, near) <= 15:
            return point, row["display_name"][:80]
    raise ValueError(f"No match within 15 km for {query!r}")


def snap(client: httpx.Client, point: list[float]) -> tuple[list[float], str, int]:
    waypoint = client.get(f"{OSRM}/nearest/v1/driving/{point[1]},{point[0]}", params={"number": 1}).json()["waypoints"][0]
    lon, lat = waypoint["location"]
    return [round(lat, 6), round(lon, 6)], waypoint.get("name") or "unnamed road", round(waypoint["distance"])


def road_distances(client: httpx.Client, homes: dict, works: dict) -> dict[tuple[str, str], float | None]:
    home_ids, work_ids = sorted(homes), sorted(works)
    result = {}
    for start in range(0, len(home_ids), TABLE_CHUNK):
        chunk = home_ids[start:start + TABLE_CHUNK]
        points = [homes[h] for h in chunk] + [works[w] for w in work_ids]
        coordinates = ";".join(f"{p[1]},{p[0]}" for p in points)
        response = client.get(f"{OSRM}/table/v1/driving/{coordinates}", params={
            "sources": ";".join(map(str, range(len(chunk)))),
            "destinations": ";".join(map(str, range(len(chunk), len(points)))), "annotations": "distance"})
        table = response.json()
        if table.get("code") != "Ok":
            raise RuntimeError(f"OSRM table failed: {table.get('message', table.get('code'))}")
        for i, home in enumerate(chunk):
            for j, work in enumerate(work_ids):
                metres = table["distances"][i][j]
                result[home, work] = None if metres is None else round(metres / 1000, 1)
        time.sleep(1)
    return result


def select_pairs(catalogue: dict, schedule: dict, homes: dict, works: dict, home_town: dict, work_town: dict) -> dict:
    """Per start place: nearest employment areas plus regional hubs, at most N per town."""
    pairs = {}
    per_town = catalogue["max_work_places_per_town"]
    random_only = catalogue.get("random_only_destinations") or {}
    random_towns = set(random_only.get("towns", []))
    for home_id, home in homes.items():
        candidates = sorted(
            (distance_km(home, works[work_id]), work_id) for work_id in works
            if work_town[work_id] != home_town[home_id] and work_town[work_id] not in random_towns
            and catalogue["candidate_minimum_straight_line_km"] <= distance_km(home, works[work_id])
            <= catalogue["maximum_regular_straight_line_km"])
        chosen: list[str] = []

        def take(limit: int, hubs_only: bool) -> None:
            added = 0
            for _, work_id in candidates:
                if added == limit:
                    return
                town = work_town[work_id]
                if work_id in chosen or (hubs_only and town not in catalogue["regional_hubs"]):
                    continue
                if sum(work_town[w] == town for w in chosen) >= per_town:
                    continue
                chosen.append(work_id)
                added += 1

        take(catalogue["nearby_work_places_per_home"], hubs_only=False)
        take(catalogue["hub_work_places_per_home"], hubs_only=True)
        for work_id in chosen:
            pairs[home_id, work_id] = {"tier": "regular", "corridor": ""}
    # Priority town pairs get every start place x employment area combination. The
    # planner still spends one call per slot on the pair, rotating through them.
    for town_pair in schedule["core_routes"]:
        home_town_id, work_town_id = town_pair.split("__", 1)
        if home_town_id not in catalogue["areas"] or work_town_id not in catalogue["areas"]:
            raise ValueError(f"Unknown priority town pair: {town_pair}")
        for home_id in (h for h in homes if home_town[h] == home_town_id):
            for work_id in (w for w in works if work_town[w] == work_town_id):
                pairs.setdefault((home_id, work_id), {"tier": "regular", "corridor": ""})
    for item in catalogue["long_distance_watchlist"]:
        if item["home"] not in homes or item["work"] not in works:
            raise ValueError(f"Unknown watchlist endpoint: {item}")
        unknown = set(item.get("corridor", [])) - catalogue["corridor_nodes"].keys()
        if unknown:
            raise ValueError(f"Unknown corridor nodes: {sorted(unknown)}")
        pairs[item["home"], item["work"]] = {"tier": "watchlist", "corridor": ";".join(item.get("corridor", []))}

    # Random-only business parks: nearest start places, one per town.
    for work_id in (w for w in works if work_town[w] in random_towns):
        towns_used = set()
        for distance, home_id in sorted((distance_km(homes[h], works[work_id]), h) for h in homes):
            if len(towns_used) == random_only["start_places_per_park"]:
                break
            if (home_town[home_id] in towns_used or distance > random_only["maximum_straight_line_km"]
                    or distance < catalogue["candidate_minimum_straight_line_km"]):
                continue
            towns_used.add(home_town[home_id])
            pairs[home_id, work_id] = {"tier": "random", "corridor": ""}

    # Random-only business parks: nearest start places, one per town.
    for work_id in (w for w in works if work_town[w] in random_towns):
        towns_used = set()
        for distance, home_id in sorted((distance_km(homes[h], works[work_id]), h) for h in homes):
            if len(towns_used) == random_only["start_places_per_park"]:
                break
            if (home_town[home_id] in towns_used or distance > random_only["maximum_straight_line_km"]
                    or distance < catalogue["candidate_minimum_straight_line_km"]):
                continue
            towns_used.add(home_town[home_id])
            pairs[home_id, work_id] = {"tier": "random", "corridor": ""}

    # One route per (home town, employment area): several neighbourhoods of one town
    # driving to the same employment area add little. Instead, the town's start places
    # are spread over its destinations: fewest routes so far first, then nearest.
    groups: dict[tuple[str, str], list[str]] = {}
    for home_id, work_id in pairs:
        groups.setdefault((home_town[home_id], work_id), []).append(home_id)
    load: Counter[str] = Counter()
    kept = {}
    for town, work_id in sorted(groups):
        candidates = groups[town, work_id]
        pool = [h for h in candidates if pairs[h, work_id]["tier"] == "watchlist"] or candidates
        home_id = min(pool, key=lambda h: (load[h], distance_km(homes[h], works[work_id]), h))
        load[home_id] += 1
        kept[home_id, work_id] = pairs[home_id, work_id]
    return kept


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="Look up and snap every point again")
    args = parser.parse_args()
    config = ROOT / "config"
    catalogue = yaml.safe_load((config / "commute_catalogue.yaml").read_text(encoding="utf-8"))
    home_places = yaml.safe_load((config / "commute_homes.yaml").read_text(encoding="utf-8"))
    anchors_path = config / "commute_anchors.yaml"
    previous = {} if args.refresh or not anchors_path.exists() else yaml.safe_load(anchors_path.read_text(encoding="utf-8"))
    client = httpx.Client(headers=USER_AGENT, timeout=60)

    homes, works, home_town, work_town = {}, {}, {}, {}
    # Home and work ids are separate namespaces: Aalst has both an Erembodegem
    # neighbourhood and the Erembodegem business area.
    names, notes = {"home": {}, "work": {}}, {"home": {}, "work": {}}
    for town, entry in home_places.items():
        if town not in catalogue["areas"]:
            raise ValueError(f"Home town {town} is not in commute_catalogue.yaml")
        for place in entry["homes"]:
            home_id = place["id"]
            home_town[home_id], names["home"][home_id] = town, place["name"]
            if home_id in previous.get("home", {}) and not args.refresh:
                homes[home_id], notes["home"][home_id] = previous["home"][home_id], "kept"
                continue
            homes[home_id], road, metres = snap(client, place["point"])
            notes["home"][home_id] = f"Statbel sector {place['sector']} ({place['sector_name']}) -> {road}, snapped {metres} m"
    for town, places in catalogue["work_places"].items():
        town_homes = [homes[h] for h in homes if home_town[h] == town] or [place["point"] for place in places if "point" in place]
        centre = (sum(p[0] for p in town_homes) / len(town_homes), sum(p[1] for p in town_homes) / len(town_homes))
        for place in places:
            work_id = f"{town}.{place['id']}"
            work_town[work_id], names["work"][work_id] = town, place["name"]
            if work_id in previous.get("work", {}) and not args.refresh:
                works[work_id], notes["work"][work_id] = previous["work"][work_id], "kept"
                continue
            point, source = (place["point"], "verified earlier") if "point" in place else geocode(client, place["query"], centre)
            works[work_id], road, metres = snap(client, point)
            notes["work"][work_id] = f"{place.get('query', place['name'])} ({source}) -> {road}, snapped {metres} m"
            print(f"work {work_id:34} {notes['work'][work_id]}", flush=True)

    schedule = yaml.safe_load((config / "commute_schedule.yaml").read_text(encoding="utf-8"))
    pairs = select_pairs(catalogue, schedule, homes, works, home_town, work_town)
    roads = road_distances(client, {h: homes[h] for h in {h for h, _ in pairs}},
                           {w: works[w] for w in {w for _, w in pairs}})
    cutoff = catalogue["minimum_distance_km"]
    rows, dropped = [], []
    for (home_id, work_id), meta in pairs.items():
        road = roads[home_id, work_id]
        if road is None or road < cutoff:
            dropped.append((home_id, work_id, road))
            continue
        rows.append({"id": f"{home_id}__{work_id}", "home_area": home_town[home_id], "home_id": home_id,
                     "home_place": names["home"][home_id], "work_area": work_town[work_id], "work_id": work_id,
                     "work_place": names["work"][work_id], "road_distance_km": road, "tier": meta["tier"],
                     "possible_corridor": meta["corridor"]})
    rows.sort(key=lambda row: (row["home_id"], row["tier"] != "watchlist", row["road_distance_km"]))

    with (config / "commute_routes.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    def block(kind: str, points: dict) -> str:
        return "\n".join(f"  {key}: [{lat}, {lon}]  # {names[kind][key]}: "
                         f"{notes[kind][key] if notes[kind][key] != 'kept' else 'verified earlier'}"
                         for key, (lat, lon) in sorted(points.items()))

    used_homes = {row["home_id"] for row in rows}
    used_works = {row["work_id"] for row in rows}
    anchors_path.write_text(
        "# Road-snapped commute endpoints, written by scripts/build_commute_catalogue.py. [latitude, longitude]\n"
        "# Homes: most populated Statbel sector of each chosen neighbourhood (scripts/select_home_places.py).\n"
        "# Works: employment areas from commute_catalogue.yaml. All points snapped with OSRM `nearest`.\n"
        f"# validated_routes: OSRM road distance home -> work; routes under {cutoff} km are not listed.\n"
        "home:\n" + block("home", {k: v for k, v in homes.items() if k in used_homes}) +
        "\nwork:\n" + block("work", {k: v for k, v in works.items() if k in used_works}) +
        "\nvalidated_routes:\n" +
        "\n".join(f"  {row['id']}: {{road_distance_km: {row['road_distance_km']}}}" for row in sorted(rows, key=lambda r: r["id"])) + "\n",
        encoding="utf-8", newline="\n")
    print(f"{len(rows)} routes from {len(used_homes)} start places to {len(used_works)} employment areas; "
          f"dropped {len(dropped)} under {cutoff} km or unroutable")


if __name__ == "__main__":
    main()
