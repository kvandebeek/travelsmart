"""Publish compact static commute data for GitHub Pages or a local server."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

import yaml

from travelsmart.commute_live import read_observations
from travelsmart.config import ROOT


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
    (output_dir / "map.json").write_text(json.dumps(build_map(routes, catalogue, all_travel, config_dir, index["generated_at"]),
                                                    separators=(",", ":")), encoding="utf-8")
    geometry = config_dir / "commute_geometry.json"  # road paths, scripts/build_map_geometry.py
    if geometry.exists():
        (output_dir / "geometry.json").write_text(geometry.read_text(encoding="utf-8"), encoding="utf-8")
    return {"months": len(months), "routes": len(dashboard_routes),
            "google_routes": len(google_routes), "observations": sum(x["calls"] for x in month_stats)}


# --- "When is it calm?" map -----------------------------------------------------------
MAP_BUCKET_MINUTES = 30
MAP_HOURS = (5, 19)            # buckets from 05:00 up to 18:30
MAP_MIN_SAMPLES = 3            # fewer measurements: shown as "not enough data yet"
# Congestion = travel time vs an empty road (provider free-flow time), as a share.
MAP_LEVELS = [{"id": "calm", "label": "Calm", "below": 0.10},
              {"id": "moderate", "label": "Moderate", "below": 0.25},
              {"id": "busy", "label": "Busy", "below": 0.45},
              {"id": "very_busy", "label": "Very busy", "below": None}]


def _bucket(observed_at: str) -> str | None:
    local = datetime.fromisoformat(observed_at).astimezone(ZoneInfo("Europe/Brussels"))
    if not MAP_HOURS[0] <= local.hour < MAP_HOURS[1]:
        return None
    minute = local.minute - local.minute % MAP_BUCKET_MINUTES
    return f"{local.hour:02d}:{minute:02d}"


def build_map(routes: list[dict], catalogue: dict, travel: list[dict], config_dir: Path, generated_at: str) -> dict:
    """Per start town, destination and half hour: how congested trips usually are, and how long they take.

    Morning measurements run home town -> employment area, evening ones the other way,
    so a town is a start point for both. Corridor business parks (random-only) are
    destinations in their own right; everything else is aggregated per town.
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
    origins = sorted({r["home_area"] for r in routes}, key=lambda key: places[key]["name"])
    destinations: dict[str, set] = defaultdict(set)
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
        if bucket is None:
            continue
        home, work = route["home_area"], work_key[route["id"]]
        origin, destination = (home, work) if row["direction"] == "morning" else (work, home)
        if origin in origins:
            samples[origin, destination, bucket].append(
                (row["duration_seconds"] / row["freeflow_seconds"] - 1, row["duration_seconds"] / 60))
    stats: dict[str, dict] = defaultdict(lambda: defaultdict(dict))
    for (origin, destination, bucket), values in samples.items():
        # [measurements, median congestion share, median travel time in whole minutes]
        stats[origin][destination][bucket] = [len(values), round(max(0.0, median(v[0] for v in values)), 3),
                                              round(median(v[1] for v in values))]
    buckets = []
    moment = datetime(2000, 1, 1, MAP_HOURS[0])
    while moment.hour < MAP_HOURS[1]:
        buckets.append(moment.strftime("%H:%M"))
        moment += timedelta(minutes=MAP_BUCKET_MINUTES)
    return {"generated_at": generated_at, "buckets": buckets, "min_samples": MAP_MIN_SAMPLES, "levels": MAP_LEVELS,
            "places": places, "origins": origins,
            "destinations": {o: sorted(destinations[o], key=lambda key: places[key]["name"]) for o in origins},
            "stats": {o: {d: dict(sorted(b.items())) for d, b in ds.items()} for o, ds in stats.items()}}
