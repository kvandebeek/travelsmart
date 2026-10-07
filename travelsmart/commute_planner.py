"""Deterministic, budgeted plans for the candidate commuter-route catalogue."""

from __future__ import annotations

import csv
import hashlib
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from travelsmart.config import ROOT


@dataclass(frozen=True)
class PlannedPoll:
    route_id: str
    direction: str  # morning: home -> work; evening: work -> home
    tier: str       # core or rotating
    scheduled_at: datetime
    account: str


def easter_sunday(year: int) -> date:
    """Gregorian computus, valid for the years supported by datetime."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f, g = (b + 8) // 25, (b - (b + 8) // 25 + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def belgian_statutory_holidays(year: int) -> set[date]:
    easter = easter_sunday(year)
    return {
        date(year, 1, 1), easter + timedelta(days=1), date(year, 5, 1),
        easter + timedelta(days=39), easter + timedelta(days=50),
        date(year, 7, 21), date(year, 8, 15), date(year, 11, 1),
        date(year, 11, 11), date(year, 12, 25),
    }


def working_days(year: int, month: int, extra_excluded_dates: list[str] = ()) -> list[date]:
    excluded = belgian_statutory_holidays(year) | {date.fromisoformat(value) for value in extra_excluded_dates}
    return [date(year, month, day) for day in range(1, monthrange(year, month)[1] + 1)
            if date(year, month, day).weekday() < 5 and date(year, month, day) not in excluded]


def load_plan_inputs(config_dir: Path = ROOT / "config", *, active_only: bool = False) -> tuple[dict, list[dict]]:
    schedule = yaml.safe_load((config_dir / "commute_schedule.yaml").read_text(encoding="utf-8"))
    with (config_dir / "commute_routes.csv").open(encoding="utf-8", newline="") as file:
        routes = list(csv.DictReader(file))
    ids = {route["id"] for route in routes}
    if len(ids) != len(routes):
        raise ValueError("Duplicate commute route IDs")
    if len(schedule["core_routes"]) != len(set(schedule["core_routes"])):
        raise ValueError("Duplicate core town pairs")
    town_pairs = {town_pair(route) for route in routes}
    if set(schedule["core_routes"]) - town_pairs:
        raise ValueError(f"Core town pairs without routes: {sorted(set(schedule['core_routes']) - town_pairs)}")
    slots = schedule["morning_slots"] + schedule["evening_slots"]
    if len(slots) != len(set(slots)) or slots != sorted(slots):
        raise ValueError("Commute slots must be unique and sorted")
    for slot in slots:
        time.fromisoformat(slot)
    if active_only:
        anchors = yaml.safe_load((config_dir / "commute_anchors.yaml").read_text(encoding="utf-8"))
        cutoff = yaml.safe_load((config_dir / "commute_catalogue.yaml").read_text(encoding="utf-8"))["minimum_distance_km"]
        approved = set()
        for route in routes:
            home = anchors["home"].get(route["home_id"])
            work = anchors["work"].get(route["work_id"])
            validation = anchors["validated_routes"].get(route["id"], {})
            if home is None or work is None or validation.get("road_distance_km", 0) < cutoff:
                continue
            for coordinates in (home, work):
                if len(coordinates) != 2 or not (-90 <= coordinates[0] <= 90 and -180 <= coordinates[1] <= 180):
                    raise ValueError(f"Invalid access coordinates for {route['id']}")
            approved.add(route["id"])
        routes = [route for route in routes if route["id"] in approved]
    return schedule, routes


def town_pair(route: dict) -> str:
    """The record a route feeds: every start place and employment area of a town counts towards it."""
    return f"{route['home_area']}__{route['work_area']}"


def _stable_rank(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def plan_month(year: int, month: int, schedule: dict, routes: list[dict]) -> list[PlannedPoll]:
    days = working_days(year, month, schedule.get("extra_excluded_dates", []))
    zone = ZoneInfo(schedule["timezone"])
    available = {route["id"] for route in routes}
    # Core town pairs are measured in every slot. Each slot uses one of the pair's
    # place-to-place routes, rotating so the town record is built from all of them.
    variants: dict[str, list[str]] = {}
    for route in routes:
        variants.setdefault(town_pair(route), []).append(route["id"])
    core = [pair for pair in schedule["core_routes"] if pair in variants]
    core_ids = {route_id for pair in core for route_id in variants[pair]}
    rotating_pool = sorted(available - core_ids)
    polls: list[PlannedPoll] = []
    for direction in ("morning", "evening"):
        slots = schedule[f"{direction}_slots"]
        account = schedule["tomtom_accounts"][direction]
        budget = account["monthly_limit"] - account["reserve"]
        core_cost = len(core) * len(days) * len(slots)
        if core_cost > budget:
            raise ValueError(f"{account['id']} core routes require {core_cost} calls; budget is {budget}")
        capacity = (budget - core_cost) // len(slots)
        first_count = min(capacity, len(rotating_pool))
        # When the catalogue outgrows the account, advance the first-sweep
        # cohort between months so every route is reached over time.
        first_offset = ((year * 12 + month) - (2026 * 12 + 10)) * first_count
        first = [rotating_pool[(first_offset + i) % len(rotating_pool)] for i in range(first_count)] if rotating_pool else []
        extra_count = min(schedule["extra_rotating_routes_per_month"], capacity - first_count, first_count)
        extra_offset = ((year * 12 + month) - (2026 * 12 + 10)) * extra_count
        extra = {first[(extra_offset + i) % len(first)] for i in range(extra_count)} if first else set()
        for day_index, day in enumerate(days):
            for slot_index, slot in enumerate(slots):
                when = datetime.combine(day, time.fromisoformat(slot), tzinfo=zone)
                for pair in core:
                    options = sorted(variants[pair])
                    choice = (day_index * len(slots) + slot_index + int(_stable_rank(pair)[:4], 16)) % len(options)
                    polls.append(PlannedPoll(options[choice], direction, "core", when, account["id"]))
        for slot in slots:
            # A rotating route is seen once per slot, with a second observation
            # for the extra cohort on a different day. Day order is stable.
            ordered = sorted(first, key=lambda route_id: _stable_rank(str(year), str(month), direction, slot, route_id))
            start = int(_stable_rank(str(year), str(month), direction, slot)[:8], 16) % len(days)
            for index, route_id in enumerate(ordered):
                first_index = (start + index) % len(days)
                when = datetime.combine(days[first_index], time.fromisoformat(slot), tzinfo=zone)
                polls.append(PlannedPoll(route_id, direction, "rotating", when, account["id"]))
                if route_id in extra:
                    second_index = (first_index + max(1, len(days)//2)) % len(days)
                    second_when = datetime.combine(days[second_index], time.fromisoformat(slot), tzinfo=zone)
                    polls.append(PlannedPoll(route_id, direction, "rotating", second_when, account["id"]))
        if sum(p.account == account["id"] for p in polls) > budget:
            raise AssertionError(f"{account['id']} plan exceeds monthly budget")
    polls.sort(key=lambda poll: (poll.scheduled_at, poll.route_id))
    return polls


def due_polls(now: datetime, schedule: dict, polls: list[PlannedPoll], max_delay_minutes: int = 30) -> list[PlannedPoll]:
    """Return the latest due slot, so delayed runs never backfill stale live traffic."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone aware")
    local = now.astimezone(ZoneInfo(schedule["timezone"]))
    due = [poll for poll in polls if timedelta(0) <= local - poll.scheduled_at <= timedelta(minutes=max_delay_minutes)]
    if not due:
        return []
    latest = max(poll.scheduled_at for poll in due)
    return [poll for poll in due if poll.scheduled_at == latest]
