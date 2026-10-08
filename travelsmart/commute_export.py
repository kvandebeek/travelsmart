"""Publish compact static commute data for GitHub Pages or a local server."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

import yaml

from travelsmart.commute_live import read_observations
from travelsmart.config import ROOT
from travelsmart.google_maps_import import google_route_id


LATEST_CALLS = 10
LATEST_FIELDS = ("observed_at", "scheduled_at", "route_id", "direction", "tier", "provider", "status", "error_type",
                 "duration_seconds", "freeflow_seconds", "traffic_delay_seconds", "distance_m")


def _allowance_used(travel: list[dict], schedule: dict) -> dict:
    """Share of each provider's monthly allowance used; the limits themselves are not published."""
    tomtom_limit = sum(account["monthly_limit"] for account in schedule["tomtom_accounts"].values())
    used = {"tomtom": round(100 * sum(row["provider"] == "tomtom" for row in travel) / tomtom_limit, 1)}
    if schedule.get("here_monthly_limit"):
        used["here"] = round(100 * sum(row["provider"] == "here" for row in travel) / schedule["here_monthly_limit"], 1)
    return used


def _google_place_id(location: str) -> str:
    return "google_" + hashlib.sha256(location.encode("utf-8")).hexdigest()[:20]


def _google_routes(travel: list[dict]) -> tuple[list[dict], dict]:
    routes = {}
    areas = {}
    for row in travel:
        if row.get("provider") != "google_maps":
            continue
        home = _google_place_id(row["origin_location"])
        work = _google_place_id(row["destination_location"])
        areas[home] = {"name": row["origin"], "region": "google_maps"}
        areas[work] = {"name": row["destination"], "region": "google_maps"}
        routes[row["route_id"]] = {
            "id": row["route_id"], "home_area": home, "home_id": home,
            "home_place": row["origin"], "work_area": work, "work_id": work,
            "work_place": row["destination"], "tier": "google",
            "road_distance_km": round(row["distance_m"] / 1000, 1) if row.get("distance_m") else None,
        }
    return list(routes.values()), areas


def export_commutes(*, config_dir: Path = ROOT / "config", data_dir: Path = ROOT / "observations",
                    output_dir: Path = ROOT / "public" / "data" / "commutes") -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    with (config_dir / "commute_routes.csv").open(encoding="utf-8", newline="") as file:
        routes = list(csv.DictReader(file))
    schedule = yaml.safe_load((config_dir / "commute_schedule.yaml").read_text(encoding="utf-8"))
    google = read_observations(data_dir / "google_maps" / "captures.jsonl")
    google_by_month: dict[str, list[dict]] = defaultdict(list)
    for row in google:
        month = datetime.fromisoformat(row["observed_at"]).astimezone(ZoneInfo("Europe/Brussels")).strftime("%Y-%m")
        google_by_month[month].append(row)
    months = sorted({path.stem for path in (data_dir / "commutes").glob("????-??.jsonl")} | set(google_by_month))
    month_stats, latest, all_travel = [], [], []
    for month in months:
        travel = read_observations(data_dir / "commutes" / f"{month}.jsonl") + google_by_month[month]
        travel.sort(key=lambda row: datetime.fromisoformat(row["observed_at"]))
        weather = {(row["provider"], row.get("account"), row["route_id"], row["observed_at"]): row
                   for row in read_observations(data_dir / "weather" / f"{month}.jsonl")}
        for row in travel:
            joined = weather.get((row["provider"], row.get("account"), row["route_id"], row["observed_at"]))
            if joined:
                row["weather"] = {"source": joined["source"], "origin": joined["origin"],
                                  "destination": joined["destination"]}
        (output_dir / f"{month}.json").write_text(json.dumps(travel, separators=(",", ":")), encoding="utf-8")
        latest = sorted(latest + travel, key=lambda row: datetime.fromisoformat(row["observed_at"]))[-LATEST_CALLS:]
        all_travel.extend(travel)
        counts = Counter(row["status"] for row in travel)
        month_stats.append({"month": month, "calls": len(travel), "successful": counts["ok"],
                            "errors": counts["error"], "too_short": counts["too_short"],
                            "weather_joined": sum("weather" in row for row in travel),
                            "allowance_used_percent": _allowance_used(travel, schedule)})
    catalogue = yaml.safe_load((config_dir / "commute_catalogue.yaml").read_text(encoding="utf-8"))
    areas = {key: {"name": area.get("name", key), "region": area["region"]} for key, area in catalogue["areas"].items()}
    google_routes, google_areas = _google_routes(google)
    areas.update(google_areas)
    dashboard_routes = routes + google_routes
    index = {"generated_at": datetime.now(timezone.utc).isoformat(), "routes": dashboard_routes, "areas": areas,
             "months": month_stats, "weather_source": "Open-Meteo historical reanalysis"}
    (output_dir / "index.json").write_text(json.dumps(index, separators=(",", ":")), encoding="utf-8")
    # A tiny file for the "latest API calls" panel, so it never loads a whole month.
    feed = [{key: row[key] for key in LATEST_FIELDS if key in row} for row in reversed(latest)]
    (output_dir / "latest.json").write_text(json.dumps({"generated_at": index["generated_at"], "calls": feed},
                                                       separators=(",", ":")), encoding="utf-8")
    calm_map = build_map(routes, catalogue, all_travel, config_dir, index["generated_at"])
    calm_map["corridors"] = build_corridors(all_travel, config_dir)
    (output_dir / "map.json").write_text(json.dumps(calm_map, separators=(",", ":")), encoding="utf-8")
    geometry = config_dir / "commute_geometry.json"  # road paths, scripts/build_map_geometry.py
    if geometry.exists():
        (output_dir / "geometry.json").write_text(geometry.read_text(encoding="utf-8"), encoding="utf-8")
    return {"months": len(months), "routes": len(dashboard_routes),
            "google_routes": len(google_routes), "observations": sum(x["calls"] for x in month_stats)}


# --- "When is it calm?" map -----------------------------------------------------------
MAP_BUCKET_MINUTES = 30
MAP_MIN_SAMPLES = 3            # fewer measurements: shown as "not enough data yet"
# Google Maps shows no free-flow time: a route's empty-road time is its own 5th-percentile travel time,
# once it has enough successful measurements for that to mean something.
MAP_BASELINE_PERCENTILE = 0.05
MAP_BASELINE_MIN_SAMPLES = 10
# Congestion = travel time vs an empty road (provider free-flow time), as a share.
MAP_LEVELS = [{"id": "calm", "label": "Calm", "below": 0.10},
              {"id": "moderate", "label": "Moderate", "below": 0.25},
              {"id": "busy", "label": "Busy", "below": 0.45},
              {"id": "very_busy", "label": "Very busy", "below": None}]


def _bucket(observed_at: str) -> str:
    """Brussels half hour of a measurement; the map covers the whole day."""
    local = datetime.fromisoformat(observed_at).astimezone(ZoneInfo("Europe/Brussels"))
    minute = local.minute - local.minute % MAP_BUCKET_MINUTES
    return f"{local.hour:02d}:{minute:02d}"


def _percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    position = share * (len(ordered) - 1)
    low = math.floor(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _municipality_places(config_dir: Path) -> tuple[dict[str, str], dict[str, dict]]:
    """Google location -> map place key, and the places themselves (scripts/build_municipality_points.py).

    A municipality that holds a commute catalogue town uses that town's key, so both data sources
    meet in one place; other municipalities get their own key.
    """
    points_file = config_dir / "belgian_municipality_points.csv"
    locations_file = config_dir / "google_maps_place_municipalities.csv"
    if not points_file.exists() or not locations_file.exists():
        return {}, {}
    with points_file.open(encoding="utf-8", newline="") as file:
        municipalities = {row["nis_code"]: row for row in csv.DictReader(file)}
    key = {code: row["map_area"] or f"nis_{code}" for code, row in municipalities.items()}
    places = {}
    for code, row in municipalities.items():  # catalogue towns normally have their own point already
        places.setdefault(key[code], {"name": row["name"], "point": [float(row["lat"]), float(row["lon"])]})
    with locations_file.open(encoding="utf-8", newline="") as file:
        location_key = {row["location"]: key[row["nis_code"]] for row in csv.DictReader(file)}
    return location_key, places


def _google_samples(travel: list[dict], location_key: dict[str, str]) -> list[tuple[str, str, dict, float]]:
    """(origin place, destination place, row, congestion share) for Google trips between two places."""
    durations: dict[str, list[float]] = defaultdict(list)
    for row in travel:
        if row.get("provider") == "google_maps" and row.get("status") == "ok" and row.get("duration_seconds"):
            durations[row["route_id"]].append(row["duration_seconds"])
    baseline = {route: _percentile(values, MAP_BASELINE_PERCENTILE)
                for route, values in durations.items() if len(values) >= MAP_BASELINE_MIN_SAMPLES}
    samples = []
    for row in travel:
        if row.get("provider") != "google_maps" or row["route_id"] not in baseline or row.get("status") != "ok":
            continue
        origin = location_key.get(row["origin_location"])
        destination = location_key.get(row["destination_location"])
        if origin and destination and origin != destination:
            samples.append((origin, destination, row, row["duration_seconds"] / baseline[row["route_id"]] - 1))
    return samples


def build_map(routes: list[dict], catalogue: dict, travel: list[dict], config_dir: Path, generated_at: str) -> dict:
    """Per start town, destination and half hour: how congested trips usually are, and how long they take.

    Morning measurements run home town -> employment area, evening ones the other way,
    so a town is a start point for both. Corridor business parks (random-only) are
    destinations in their own right; everything else is aggregated per town. Google Maps
    trips join per municipality, measured against their route's own fastest times.
    """
    anchors = yaml.safe_load((config_dir / "commute_anchors.yaml").read_text(encoding="utf-8"))
    areas = catalogue["areas"]
    corridor = {key for key, area in areas.items() if area.get("region") == "corridor"}
    park_names = {f"{town}.{place['id']}": place["name"] for town, places in catalogue["work_places"].items()
                  for place in places}
    work_key = {r["id"]: (r["work_id"] if r["work_area"] in corridor else r["work_area"]) for r in routes}
    points: dict[str, list] = defaultdict(list)
    for route in routes:
        points[route["home_area"]].append(anchors["home"][route["home_id"]])
        points[work_key[route["id"]]].append(anchors["work"][route["work_id"]])
    places = {key: {"name": (f"{areas[key.split('.')[0]]['name']} · {park_names[key]}" if "." in key else areas[key]["name"]),
                    "point": [round(sum(p[0] for p in pts) / len(pts), 5), round(sum(p[1] for p in pts) / len(pts), 5)]}
              for key, pts in points.items()}
    location_key, municipality_places = _municipality_places(config_dir)
    google = _google_samples(travel, location_key)
    for origin, destination, _, _ in google:
        for key in (origin, destination):
            if key not in places:
                places[key] = {**municipality_places[key], **({"name": areas[key]["name"]} if key in areas else {})}
    origins = sorted({r["home_area"] for r in routes} | {origin for origin, _, _, _ in google},
                     key=lambda key: places[key]["name"])
    destinations: dict[str, set] = defaultdict(set)
    for origin, destination, _, _ in google:
        destinations[origin].add(destination)
    for route in routes:
        destinations[route["home_area"]].add(work_key[route["id"]])
        if work_key[route["id"]] in origins:
            destinations[work_key[route["id"]]].add(route["home_area"])
    by_id = {r["id"]: r for r in routes}
    samples: dict[tuple, list[tuple[float, float]]] = defaultdict(list)
    for row in travel:
        route = by_id.get(row["route_id"])
        if not route or row.get("status") != "ok" or not row.get("freeflow_seconds"):
            continue
        bucket = _bucket(row["observed_at"])
        home, work = route["home_area"], work_key[route["id"]]
        origin, destination = (home, work) if row["direction"] == "morning" else (work, home)
        if origin in origins:
            samples[origin, destination, bucket].append(
                (row["duration_seconds"] / row["freeflow_seconds"] - 1, row["duration_seconds"] / 60))
    for origin, destination, row, share in google:
        samples[origin, destination, _bucket(row["observed_at"])].append((share, row["duration_seconds"] / 60))
    stats: dict[str, dict] = defaultdict(lambda: defaultdict(dict))
    for (origin, destination, bucket), values in samples.items():
        # [measurements, median congestion share, median travel time in whole minutes]
        stats[origin][destination][bucket] = [len(values), round(max(0.0, median(v[0] for v in values)), 3),
                                              round(median(v[1] for v in values))]
    buckets = [f"{minute // 60:02d}:{minute % 60:02d}" for minute in range(0, 24 * 60, MAP_BUCKET_MINUTES)]
    return {"generated_at": generated_at, "buckets": buckets, "min_samples": MAP_MIN_SAMPLES, "levels": MAP_LEVELS,
            "places": places, "origins": origins,
            "destinations": {o: sorted(destinations[o], key=lambda key: places[key]["name"]) for o in origins},
            "stats": {o: {d: dict(sorted(b.items())) for d, b in ds.items()} for o, ds in stats.items()}}


# --- Corridor strips -----------------------------------------------------------------------
# Each Google Maps corridor leg is compared with its own usual time: the median of its half-hour
# medians, so hours that happen to be measured more often do not dominate. Needs data in at least
# CORRIDOR_USUAL_MIN_BUCKETS half hours. Any collector's captures between the same two locations count.
CORRIDOR_USUAL_MIN_BUCKETS = 4
CORRIDOR_LEVELS = [{"id": "quieter", "label": "Quieter than usual", "below": -0.05},
                   {"id": "usual", "label": "As usual", "below": 0.10},
                   {"id": "slower", "label": "Slower than usual", "below": 0.30},
                   {"id": "much_slower", "label": "Much slower than usual", "below": None}]
COORDINATES = re.compile(r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*")


def _location_point(location: str, municipality_point: dict[str, list[float]]) -> list[float] | None:
    """A location's own coordinates, else its municipality's main point (place-name queries)."""
    match = COORDINATES.fullmatch(location)
    return [float(match[1]), float(match[2])] if match else municipality_point.get(location)


def build_corridors(travel: list[dict], config_dir: Path) -> dict:
    corridors_file = config_dir / "google_maps_stretch_corridors.yaml"
    if not corridors_file.exists():
        return {"levels": CORRIDOR_LEVELS, "points": {}, "corridors": []}
    points = {}
    for name in ("google_maps_points.csv", "google_maps_stretch_points.csv"):
        with (config_dir / name).open(encoding="utf-8-sig", newline="") as file:
            points.update({row["id"].strip(): {"name": row["name"].strip(), "location": row["location"].strip()}
                           for row in csv.DictReader(file) if row.get("id")})
    municipality_point = {}
    points_file = config_dir / "belgian_municipality_points.csv"
    locations_file = config_dir / "google_maps_place_municipalities.csv"
    if points_file.exists() and locations_file.exists():
        with points_file.open(encoding="utf-8", newline="") as file:
            by_code = {row["nis_code"]: [float(row["lat"]), float(row["lon"])] for row in csv.DictReader(file)}
        with locations_file.open(encoding="utf-8", newline="") as file:
            municipality_point = {row["location"]: by_code[row["nis_code"]] for row in csv.DictReader(file)}
    minutes: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in travel:
        if row.get("provider") == "google_maps" and row.get("status") == "ok" and row.get("duration_seconds"):
            minutes[row["route_id"]][_bucket(row["observed_at"])].append(row["duration_seconds"] / 60)
    corridors, used = [], set()
    for corridor in yaml.safe_load(corridors_file.read_text(encoding="utf-8"))["corridors"]:
        nodes = corridor["nodes"]
        if any(node not in points or not _location_point(points[node]["location"], municipality_point) for node in nodes):
            continue  # a point without a known position cannot be drawn
        directions = {}
        for direction, order in (("forward", nodes), ("reverse", nodes[::-1])):
            legs = []
            for origin, destination in zip(order, order[1:]):
                by_bucket = minutes.get(google_route_id(points[origin]["location"], points[destination]["location"]), {})
                medians = {bucket: median(values) for bucket, values in by_bucket.items()}
                usual = median(medians.values()) if len(medians) >= CORRIDOR_USUAL_MIN_BUCKETS else None
                # stats per half hour: [measurements, share above (+) or below (-) usual, median minutes]
                legs.append({"from": origin, "to": destination, "usual": round(usual, 1) if usual else None,
                             "stats": {bucket: [len(by_bucket[bucket]), round(value / usual - 1, 3), round(value)]
                                       for bucket, value in sorted(medians.items())} if usual else {}})
            directions[direction] = legs
        used.update(nodes)
        corridors.append({"id": corridor["id"], "name": corridor["name"], "directions": directions})
    return {"levels": CORRIDOR_LEVELS, "usual_min_buckets": CORRIDOR_USUAL_MIN_BUCKETS,
            "points": {key: {"name": points[key]["name"],
                             "point": _location_point(points[key]["location"], municipality_point)} for key in sorted(used)},
            "corridors": corridors}
