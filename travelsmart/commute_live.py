"""Guarded live commute collection and portable JSONL observation storage."""

from __future__ import annotations

import json
import os
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import yaml

from travelsmart.commute_context import calendar_context, event_context, fetch_datex_snapshot
from travelsmart.commute_planner import PlannedPoll, due_polls, load_plan_inputs, plan_month
from travelsmart.config import ROOT


def _coord_text(point: list[float]) -> str:
    return f"{point[0]:.6f},{point[1]:.6f}"


def fetch_live_route(provider: str, origin: list[float], destination: list[float], key: str) -> dict:
    """One provider request. No future departure time is supplied: this measures now."""
    if provider == "tomtom":
        url = f"https://api.tomtom.com/routing/1/calculateRoute/{_coord_text(origin)}:{_coord_text(destination)}/json"
        response = httpx.get(url, params={"key": key, "traffic": "true", "departAt": "now",
                                           "travelMode": "car", "routeRepresentation": "polyline",
                                           "computeTravelTimeFor": "all"}, timeout=30)
        response.raise_for_status()
        route = response.json()["routes"][0]
        summary = route["summary"]
        return {"distance_m": summary["lengthInMeters"], "duration_seconds": summary["travelTimeInSeconds"],
                "traffic_delay_seconds": summary.get("trafficDelayInSeconds"),
                "freeflow_seconds": summary.get("noTrafficTravelTimeInSeconds"),
                "route_points": [(point["latitude"], point["longitude"])
                                 for leg in route.get("legs", []) for point in leg.get("points", [])]}
    if provider == "here":
        response = httpx.get("https://router.hereapi.com/v8/routes",
                             params={"apiKey": key, "transportMode": "car", "origin": _coord_text(origin),
                                     "destination": _coord_text(destination), "return": "summary"}, timeout=30)
        response.raise_for_status()
        sections = response.json()["routes"][0]["sections"]
        return {"distance_m": sum(section["summary"]["length"] for section in sections),
                "duration_seconds": sum(section["summary"]["duration"] for section in sections),
                "traffic_delay_seconds": None,
                "freeflow_seconds": sum(section["summary"].get("baseDuration", 0) for section in sections),
                "route_points": None}
    raise ValueError(f"Unknown provider: {provider}")


def observation_path(data_dir: Path, when: datetime) -> Path:
    return data_dir / "commutes" / f"{when:%Y-%m}.jsonl"


def read_observations(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def append_observation(path: Path, observation: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as file:
        file.write(json.dumps(observation, ensure_ascii=False, separators=(",", ":")) + "\n")


def run_tick(now: datetime, *, provider: str = "tomtom", execute: bool = False,
             config_dir: Path = ROOT / "config", data_dir: Path = ROOT / "observations") -> dict:
    """Run the latest due slot; execution requires validated anchors and a key."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone aware")
    if provider not in ("tomtom", "here"):
        raise ValueError(f"Unknown provider: {provider}")
    schedule, routes = load_plan_inputs(config_dir, active_only=True)
    if provider == "here" and schedule["here_daily_limit"] <= 0:
        return {"due": 0, "attempted": 0, "stored": 0, "reason": "here_disabled"}
    local = now.astimezone(ZoneInfo(schedule["timezone"]))
    polls = due_polls(now, schedule, plan_month(local.year, local.month, schedule, routes))
    if provider == "here":
        polls = sorted(polls, key=lambda poll: hashlib.sha256(
            f"here|{poll.scheduled_at.isoformat()}|{poll.route_id}".encode()).hexdigest())[:schedule["here_routes_per_slot"]]
    if not polls or not execute:
        return {"due": len(polls), "attempted": 0, "stored": 0, "reason": "dry_run" if not execute else "nothing_due"}
    accounts = {item["id"]: item for item in schedule["tomtom_accounts"].values()}
    needed_keys = ({accounts[poll.account]["key_env"] for poll in polls} if provider == "tomtom" else {"HERE_API_KEY"})
    missing_keys = [name for name in needed_keys if not os.getenv(name)]
    if missing_keys:
        raise RuntimeError(f"Missing live API key environment variables: {', '.join(sorted(missing_keys))}")
    anchors = yaml.safe_load((config_dir / "commute_anchors.yaml").read_text(encoding="utf-8"))
    snapshot = None
    if provider == "tomtom":
        try:
            source = yaml.safe_load((config_dir / "sources.yaml").read_text(encoding="utf-8"))["datex"]["url"]
            snapshot = fetch_datex_snapshot(source)
        except (httpx.HTTPError, KeyError, ValueError):
            pass  # Null context means unavailable, never "zero incidents".
    route_map = {route["id"]: route for route in routes}
    path = observation_path(data_dir, local)
    existing = read_observations(path)
    completed = {(row["provider"], row.get("account"), row["route_id"], row["scheduled_at"]) for row in existing}
    monthly_used = {account_id: sum(row["provider"] == "tomtom" and row.get("account") == account_id
                                    for row in existing) for account_id in accounts}
    here_used_today = sum(row["provider"] == "here" and
                          datetime.fromisoformat(row["observed_at"]).astimezone(ZoneInfo(schedule["timezone"])).date() == local.date()
                          for row in existing)
    attempts = stored = 0
    for poll in polls:
        account_id = poll.account if provider == "tomtom" else "here"
        identity = (provider, account_id, poll.route_id, poll.scheduled_at.isoformat())
        if identity in completed:
            continue
        if provider == "tomtom":
            account_config = accounts[account_id]
            if monthly_used[account_id] >= account_config["monthly_limit"] - account_config["reserve"]:
                break
        if provider == "here" and here_used_today >= schedule["here_daily_limit"]:
            break
        route = route_map[poll.route_id]
        home = anchors["home"][route["home_area"]]
        work = anchors["work"][route["work_area"]]
        origin, destination = (home, work) if poll.direction == "morning" else (work, home)
        observed = datetime.now(timezone.utc)
        row = {"provider": provider, "account": account_id, "route_id": poll.route_id, "direction": poll.direction,
               "tier": poll.tier, "scheduled_at": poll.scheduled_at.isoformat(),
               "observed_at": observed.isoformat(), "status": "error"}
        attempts += 1
        if provider == "tomtom":
            monthly_used[account_id] += 1
        else:
            here_used_today += 1
        try:
            key = os.environ[accounts[account_id]["key_env"]] if provider == "tomtom" else os.environ["HERE_API_KEY"]
            result = fetch_live_route(provider, origin, destination, key)
            if result["distance_m"] < 12000:
                row["status"] = "too_short"
            else:
                route_points = result.pop("route_points")
                row.update(result)
                row["calendar"] = calendar_context(observed, origin, destination, result["duration_seconds"])
                row["traffic_events"] = event_context(route_points, snapshot, observed)
                row["status"] = "ok"
                stored += 1
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as error:
            # Do not serialize exception strings: request URLs may contain keys.
            row["error_type"] = type(error).__name__
        append_observation(path, row)
        completed.add(identity)
    return {"due": len(polls), "attempted": attempts, "stored": stored, "reason": "executed"}
