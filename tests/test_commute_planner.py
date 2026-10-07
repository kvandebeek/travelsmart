from collections import Counter
from datetime import date, datetime
from zoneinfo import ZoneInfo

from travelsmart.commute_planner import (belgian_statutory_holidays, due_polls,
                                         load_plan_inputs, plan_month, working_days)


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
    assert len(polls) == 27984
    assert Counter(poll.tier for poll in polls) == {"core": 10560, "rotating": 17424}
    assert Counter(poll.account for poll in polls) == {"tomtom_morning": 17490, "tomtom_evening": 10494}
    assert len({poll.route_id for poll in polls}) == 446
    assert all(poll.scheduled_at.weekday() < 5 for poll in polls)
    assert len({(p.route_id, p.scheduled_at) for p in polls}) == len(polls)
    # Every rotating route has one or two measurements per slot during the month.
    rotating_slots = Counter((p.route_id, p.scheduled_at.strftime("%H:%M")) for p in polls if p.tier == "rotating")
    assert set(rotating_slots.values()) == {1, 2}


def test_due_slot_discards_stale_work_and_no_unverified_routes():
    schedule, routes = load_plan_inputs(active_only=True)
    assert routes == []
    assert plan_month(2026, 10, schedule, routes) == []
    schedule, routes = load_plan_inputs()
    polls = plan_month(2026, 10, schedule, routes)
    local = ZoneInfo("Europe/Brussels")
    due = due_polls(datetime(2026, 10, 7, 7, 18, tzinfo=local), schedule, polls)
    assert due and {p.scheduled_at.strftime("%H:%M") for p in due} == {"07:10"}
    assert due_polls(datetime(2026, 10, 7, 9, 40, tzinfo=local), schedule, polls) == []
    assert due_polls(datetime(2026, 10, 10, 7, 18, tzinfo=local), schedule, polls) == []
