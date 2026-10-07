"""Publish compact static commute data for GitHub Pages or a local server."""

from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

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


def export_commutes(*, config_dir: Path = ROOT / "config", data_dir: Path = ROOT / "observations",
                    output_dir: Path = ROOT / "public" / "data" / "commutes") -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    with (config_dir / "commute_routes.csv").open(encoding="utf-8", newline="") as file:
        routes = list(csv.DictReader(file))
    schedule = yaml.safe_load((config_dir / "commute_schedule.yaml").read_text(encoding="utf-8"))
    months = sorted(path.stem for path in (data_dir / "commutes").glob("????-??.jsonl"))
    month_stats, latest = [], []
    for month in months:
        travel = read_observations(data_dir / "commutes" / f"{month}.jsonl")
        weather = {(row["provider"], row.get("account"), row["route_id"], row["observed_at"]): row
                   for row in read_observations(data_dir / "weather" / f"{month}.jsonl")}
        for row in travel:
            joined = weather.get((row["provider"], row.get("account"), row["route_id"], row["observed_at"]))
            if joined:
                row["weather"] = {"source": joined["source"], "origin": joined["origin"],
                                  "destination": joined["destination"]}
        (output_dir / f"{month}.json").write_text(json.dumps(travel, separators=(",", ":")), encoding="utf-8")
        latest = sorted(latest + travel, key=lambda row: row["observed_at"])[-LATEST_CALLS:]
        counts = Counter(row["status"] for row in travel)
        month_stats.append({"month": month, "calls": len(travel), "successful": counts["ok"],
                            "errors": counts["error"], "too_short": counts["too_short"],
                            "weather_joined": sum("weather" in row for row in travel),
                            "allowance_used_percent": _allowance_used(travel, schedule)})
    catalogue = yaml.safe_load((config_dir / "commute_catalogue.yaml").read_text(encoding="utf-8"))
    areas = {key: {"name": area.get("name", key), "region": area["region"]} for key, area in catalogue["areas"].items()}
    index = {"generated_at": datetime.now(timezone.utc).isoformat(), "routes": routes, "areas": areas,
             "months": month_stats, "weather_source": "Open-Meteo historical reanalysis"}
    (output_dir / "index.json").write_text(json.dumps(index, separators=(",", ":")), encoding="utf-8")
    # A tiny file for the "latest API calls" panel, so it never loads a whole month.
    feed = [{key: row[key] for key in LATEST_FIELDS if key in row} for row in reversed(latest)]
    (output_dir / "latest.json").write_text(json.dumps({"generated_at": index["generated_at"], "calls": feed},
                                                       separators=(",", ":")), encoding="utf-8")
    return {"months": len(months), "routes": len(routes), "observations": sum(x["calls"] for x in month_stats)}
