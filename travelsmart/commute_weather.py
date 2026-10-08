"""Join recorded commutes to hourly historical weather at their endpoints."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import yaml

from travelsmart.commute_live import append_observation, observation_path, read_observations
from travelsmart.config import ROOT

HOURLY = ("temperature_2m", "precipitation", "wind_speed_10m", "wind_gusts_10m")
SOURCE = "open_meteo_historical_reanalysis"


def fetch_weather(point: list[float], first_day: date, last_day: date) -> dict[str, dict]:
    response = httpx.get("https://archive-api.open-meteo.com/v1/archive",
                         params={"latitude": point[0], "longitude": point[1],
                                 "start_date": first_day.isoformat(), "end_date": last_day.isoformat(),
                                 "hourly": ",".join(HOURLY), "timezone": "Europe/Brussels"}, timeout=30)
    response.raise_for_status()
    data = response.json()["hourly"]
    return {hour: {name: data[name][index] for name in HOURLY}
            for index, hour in enumerate(data["time"])}


def weather_at(hours: dict[str, dict], when: datetime) -> dict | None:
    key = when.strftime("%Y-%m-%dT%H:00")
    values = hours.get(key)
    return {"hour": key, **values} if values and all(value is not None for value in values.values()) else None


def enrich_day(day: date, *, config_dir: Path = ROOT / "config", data_dir: Path = ROOT / "observations") -> dict:
    anchors = yaml.safe_load((config_dir / "commute_anchors.yaml").read_text(encoding="utf-8"))
    zone = ZoneInfo("Europe/Brussels")
    path = observation_path(data_dir, datetime.combine(day, datetime.min.time()))
    observations = [row for row in read_observations(path) if row["provider"] in ("tomtom", "here") and row["status"] == "ok" and
                    datetime.fromisoformat(row["observed_at"]).astimezone(zone).date() == day]
    output = data_dir / "weather" / f"{day:%Y-%m}.jsonl"
    existing = {(row["provider"], row.get("account"), row["route_id"], row["observed_at"]) for row in read_observations(output)}
    cache: dict[tuple[str, str], dict[str, dict]] = {}
    added = 0
    for row in observations:
        identity = (row["provider"], row.get("account"), row["route_id"], row["observed_at"])
        if identity in existing:
            continue
        home_id, work_id = row["route_id"].split("__", 1)  # route ids are <home_id>__<work_id>
        origin_kind, origin_area = ("home", home_id) if row["direction"] == "morning" else ("work", work_id)
        dest_kind, dest_area = ("work", work_id) if row["direction"] == "morning" else ("home", home_id)
        if origin_area not in anchors[origin_kind] or dest_area not in anchors[dest_kind]:
            continue
        observed = datetime.fromisoformat(row["observed_at"]).astimezone(zone)
        arrival = observed + timedelta(seconds=row["duration_seconds"])

        def at(kind: str, area: str, when: datetime) -> dict | None:
            key = kind, area
            if key not in cache:
                cache[key] = fetch_weather(anchors[kind][area], day, day + timedelta(days=1))
            return weather_at(cache[key], when)

        origin_weather = at(origin_kind, origin_area, observed)
        destination_weather = at(dest_kind, dest_area, arrival)
        if origin_weather is None or destination_weather is None:
            continue
        append_observation(output, {"provider": row["provider"], "account": row.get("account"), "route_id": row["route_id"],
                                    "direction": row["direction"], "observed_at": row["observed_at"],
                                    "source": SOURCE, "origin": origin_weather, "destination": destination_weather})
        existing.add(identity)
        added += 1
    return {"date": day.isoformat(), "travel_observations": len(observations),
            "weather_joined": added, "areas_fetched": len(cache)}
