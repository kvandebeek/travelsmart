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


def export_commutes(*, config_dir: Path = ROOT / "config", data_dir: Path = ROOT / "observations",
                    output_dir: Path = ROOT / "public" / "data" / "commutes") -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    with (config_dir / "commute_routes.csv").open(encoding="utf-8", newline="") as file:
        routes = list(csv.DictReader(file))
    schedule = yaml.safe_load((config_dir / "commute_schedule.yaml").read_text(encoding="utf-8"))
    months = sorted(path.stem for path in (data_dir / "commutes").glob("????-??.jsonl"))
    month_stats = []
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
        counts = Counter(row["status"] for row in travel)
        month_stats.append({"month": month, "calls": len(travel), "successful": counts["ok"],
                            "errors": counts["error"], "too_short": counts["too_short"],
                            "weather_joined": sum("weather" in row for row in travel)})
    budgets = {item["id"]: {"monthly_limit": item["monthly_limit"], "reserve": item["reserve"]}
               for item in schedule["tomtom_accounts"].values()}
    index = {"generated_at": datetime.now(timezone.utc).isoformat(), "routes": routes,
             "account_budgets": budgets,
             "months": month_stats, "weather_source": "Open-Meteo historical reanalysis"}
    (output_dir / "index.json").write_text(json.dumps(index, separators=(",", ":")), encoding="utf-8")
    return {"months": len(months), "routes": len(routes), "observations": sum(x["calls"] for x in month_stats)}
