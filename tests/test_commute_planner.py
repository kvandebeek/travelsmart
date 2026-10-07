import json
import shutil
from collections import Counter
from datetime import date, datetime
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
    assert {poll.route_id for poll in polls} == {route["id"] for route in routes}
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
    polls = plan_month(2026, 10, schedule, routes)
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
