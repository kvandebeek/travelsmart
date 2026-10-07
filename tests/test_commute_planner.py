import json
import shutil
from collections import Counter
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import yaml

from travelsmart import commute_live
from travelsmart.commute_planner import (belgian_statutory_holidays, due_polls,
                                         load_plan_inputs, plan_month, working_days)
from travelsmart.config import ROOT


def test_statutory_holidays_and_october_budget():
    holidays = belgian_statutory_holidays(2026)
    assert date(2026, 4, 6) in holidays  # Easter Monday
    assert date(2026, 5, 14) in holidays  # Ascension
    assert date(2026, 5, 25) in holidays  # Whit Monday
    assert date(2026, 11, 11) in holidays
    assert len(working_days(2026, 10)) == 22
    assert date(2026, 11, 11) not in working_days(2026, 11)
    assert date(2026, 10, 8) not in working_days(2026, 10, ["2026-10-08"])
    schedule, routes = load_plan_inputs()
    polls = plan_month(2026, 10, schedule, routes)
    slots = len(schedule["morning_slots"]) + len(schedule["evening_slots"])
    assert Counter(poll.tier for poll in polls)["core"] == len(schedule["core_routes"]) * 22 * slots
    for account in schedule["tomtom_accounts"].values():
        assert sum(p.account == account["id"] for p in polls) <= account["monthly_limit"] - account["reserve"]
    assert {poll.route_id for poll in polls} >= {route["id"] for route in routes if route["tier"] != "random"}
    assert all(poll.scheduled_at.weekday() < 5 for poll in polls)
    assert len({(p.route_id, p.scheduled_at) for p in polls}) == len(polls)
    # Every rotating route has one or two measurements per slot during the month.
    rotating_slots = Counter((p.route_id, p.scheduled_at.strftime("%H:%M")) for p in polls if p.tier == "rotating")
    assert set(rotating_slots.values()) == {1, 2}


def test_due_slot_discards_stale_work_and_only_verified_routes_are_active():
    schedule, active = load_plan_inputs(active_only=True)
    anchors = yaml.safe_load((ROOT / "config" / "commute_anchors.yaml").read_text(encoding="utf-8"))
    assert active
    for route in active:
        assert route["home_id"] in anchors["home"] and route["work_id"] in anchors["work"]
        assert anchors["validated_routes"][route["id"]]["road_distance_km"] >= 12
    schedule, routes = load_plan_inputs()
    polls = [p for p in plan_month(2026, 10, schedule, routes) if p.tier != "extra"]  # regular stream, as run_tick does
    local = ZoneInfo("Europe/Brussels")
    due = due_polls(datetime(2026, 10, 7, 7, 18, tzinfo=local), schedule, polls)
    assert due and {p.scheduled_at.strftime("%H:%M") for p in due} == {"07:10"}
    assert due_polls(datetime(2026, 10, 7, 9, 40, tzinfo=local), schedule, polls) == []
    assert due_polls(datetime(2026, 10, 10, 7, 18, tzinfo=local), schedule, polls) == []


def _config_copy(tmp_path, **overrides):
    config = tmp_path / "config"
    shutil.copytree(ROOT / "config", config, ignore=shutil.ignore_patterns("certs"))
    schedule = yaml.safe_load((config / "commute_schedule.yaml").read_text(encoding="utf-8"))
    schedule.update(overrides)
    (config / "commute_schedule.yaml").write_text(yaml.safe_dump(schedule), encoding="utf-8")
    return config


def test_here_monthly_cap_stops_paid_calls(tmp_path, monkeypatch):
    config = _config_copy(tmp_path, here_monthly_limit=1)
    data = tmp_path / "observations"
    used = {"provider": "here", "account": "here", "route_id": "x", "direction": "morning",
            "scheduled_at": "2026-10-01T07:10:00+02:00", "observed_at": "2026-10-01T05:10:00+00:00", "status": "ok"}
    (data / "commutes").mkdir(parents=True)
    (data / "commutes" / "2026-10.jsonl").write_text(json.dumps(used) + "\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(commute_live, "fetch_live_route", lambda *args: calls.append(args))
    monkeypatch.setenv("HERE_API_KEY", "test")
    now = datetime(2026, 10, 7, 7, 12, tzinfo=ZoneInfo("Europe/Brussels"))
    result = commute_live.run_tick(now, provider="here", execute=True, config_dir=config, data_dir=data)
    assert result["due"] > 0 and result["attempted"] == 0 and calls == []


def test_missing_account_key_skips_without_borrowing(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(commute_live, "fetch_live_route", lambda *args: calls.append(args))
    monkeypatch.setenv("TOMTOM_API_KEY", "morning-only")
    monkeypatch.delenv("TOMTOM_API_KEY2", raising=False)
    now = datetime(2026, 10, 7, 17, 5, tzinfo=ZoneInfo("Europe/Brussels"))
    result = commute_live.run_tick(now, execute=True, config_dir=ROOT / "config", data_dir=tmp_path)
    assert result["reason"] == "missing_keys" and result["missing_keys"] == ["TOMTOM_API_KEY2"]
    assert calls == [] and not (tmp_path / "commutes").exists()


def test_peak_extra_stream_draws_from_its_list():
    schedule, routes = load_plan_inputs()
    config = schedule["extra_routes"][0]  # the peak stream

    def in_peak(poll):
        return poll.tier == "extra" and any(time.fromisoformat(s) <= poll.scheduled_at.time() < time.fromisoformat(e)
                                            for s, e in config["windows"].values())

    extra = [p for p in plan_month(2026, 10, schedule, routes) if in_peak(p)]
    per_day = sum(
        (datetime.strptime(end, "%H:%M") - datetime.strptime(start, "%H:%M")).seconds // 60 // config["every_minutes"]
        + (1 if (datetime.strptime(end, "%H:%M") - datetime.strptime(start, "%H:%M")).seconds // 60 % config["every_minutes"] else 0)
        for start, end in config["windows"].values())
    assert len(extra) == 22 * per_day
    by_id = {route["id"]: route for route in routes}
    listed = set(config["routes"])
    assert all(p.route_id in listed or f"{by_id[p.route_id]['home_area']}__{by_id[p.route_id]['work_area']}" in listed
               for p in extra)
    account = schedule["tomtom_accounts"][config["account"]]["id"]
    assert {p.account for p in extra} == {account}
    assert {p.direction for p in extra if p.scheduled_at.hour < 12} == {"morning"}
    assert len({p.route_id for p in extra}) > len(listed)  # town pairs rotate over their places
    assert extra == [p for p in plan_month(2026, 10, schedule, routes) if in_peak(p)]  # reproducible
    tick = extra[len(extra) // 3].scheduled_at
    due = due_polls(tick + timedelta(minutes=2), schedule, extra)
    assert [p.scheduled_at for p in due] == [tick]


def _cron_values(field: str, low: int, high: int) -> set[int]:
    values = set()
    for part in field.split(","):
        part, _, step = part.partition("/")
        first, last = (low, high) if part == "*" else (int(part.split("-")[0]), int(part.split("-")[-1]))
        values.update(range(first, last + 1, int(step or 1)))
    return values


def _cron_times(workflow: str) -> set[str]:
    config = yaml.safe_load((ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8"))
    times = set()
    for entry in config[True]["schedule"]:  # YAML 1.1 reads the key `on` as True
        assert "timezone" not in entry, "schedules are plain UTC"
        minute, hour, *_ = entry["cron"].split()
        times |= {f"{h:02d}:{m:02d}" for h in _cron_values(hour, 0, 23) for m in _cron_values(minute, 0, 59)}
    return times


def test_workflow_triggers_cover_every_planned_time():
    """Crons are UTC; every Brussels time must be covered in winter (UTC+1) and summer (UTC+2)."""
    schedule, routes = load_plan_inputs()
    polls = plan_month(2026, 10, schedule, routes)

    def utc(hhmm: str, offset: int) -> str:
        hour, minute = map(int, hhmm.split(":"))
        return f"{(hour - offset) % 24:02d}:{minute:02d}"

    for workflow, tiers in (("commutes.yml", {"core", "rotating"}), ("extra-routes.yml", {"extra"})):
        planned = {p.scheduled_at.strftime("%H:%M") for p in polls if p.tier in tiers}
        missing = sorted(f"{t}+{o}" for t in planned for o in (1, 2) if utc(t, o) not in _cron_times(workflow))
        assert not missing, (workflow, missing)


def test_daytime_stream_draws_from_everything_and_both_directions():
    schedule, routes = load_plan_inputs()
    polls = plan_month(2026, 10, schedule, routes)
    daytime = [p for p in polls if p.tier == "extra" and time(9) <= p.scheduled_at.time() < time(14)]
    per_tick = schedule["extra_routes"][1]["per_tick"]
    assert len(daytime) == 22 * 60 * per_tick
    assert len({(p.route_id, p.scheduled_at) for p in daytime}) == len(daytime)  # distinct routes per tick
    assert {p.direction for p in daytime} == {"morning", "evening"}
    random_only = {r["id"] for r in routes if r["tier"] == "random"}
    assert random_only and random_only & {p.route_id for p in daytime}
    assert not random_only & {p.route_id for p in polls if p.tier in ("core", "rotating")}


def test_latest_feed_has_the_ten_newest_calls(tmp_path):
    from travelsmart.commute_export import export_commutes
    data = tmp_path / "observations" / "commutes"
    data.mkdir(parents=True)
    for month, day0 in (("2026-09", 1), ("2026-10", 1)):
        rows = [{"provider": "tomtom", "account": "tomtom_morning", "route_id": "x__y", "direction": "morning",
                 "tier": "core", "scheduled_at": f"{month}-{day0 + i:02d}T07:10:00+02:00",
                 "observed_at": f"{month}-{day0 + i:02d}T05:10:00+00:00", "status": "ok", "duration_seconds": 60 * i}
                for i in range(8)]
        (data / f"{month}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    out = tmp_path / "public"
    export_commutes(data_dir=tmp_path / "observations", output_dir=out)
    feed = json.loads((out / "latest.json").read_text(encoding="utf-8"))["calls"]
    assert len(feed) == 10
    assert feed[0]["observed_at"] == "2026-10-08T05:10:00+00:00"  # newest first
    assert feed[-1]["observed_at"].startswith("2026-09-07")
    assert all("account" not in row for row in feed)
