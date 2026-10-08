"""Paced spending against each provider's free tier (§6.5)."""

from datetime import datetime, timedelta, timezone

import pytest

from travelsmart.db import LegMeasurement, make_session_factory
from travelsmart.measure.budget import BUDGETS, Budget, may_request, remaining_today, spent_since


@pytest.fixture
def session(tmp_path):
    factory = make_session_factory(f"sqlite:///{tmp_path}/budget.db")
    with factory() as open_session:
        yield open_session


def leg(provider, chain_id, created_at, edge_id="e1"):
    return LegMeasurement(edge_id=edge_id, provider=provider, chain_id=chain_id,
                          departure_utc=created_at, weekday=0, half_hour=0, seconds=100.0,
                          accepted=True, created_at=created_at)


def test_two_accounts_double_the_monthly_tier():
    one = Budget("x", per_30_days=20_000)
    two = Budget("x", per_30_days=20_000, accounts=2)
    assert two.daily_allowance() == 2 * one.daily_allowance()


def test_the_tightest_limit_wins():
    # HERE may take 96 a day, but 3,000 a month is only 100 a day, so the daily cap binds.
    budget = Budget("here", per_day=96, per_30_days=3_000)
    assert budget.daily_allowance() == int(96 * 0.9)


def test_a_provider_without_a_limit_cannot_be_paced():
    with pytest.raises(ValueError):
        Budget("nameless").daily_allowance()


def test_requests_are_spread_over_the_whole_day():
    spacing = BUDGETS["here"].seconds_between_requests()
    assert 800 < spacing < 1200          # ~86 requests a day is roughly one every quarter of an hour


def test_a_chain_counts_as_one_request_however_many_legs_it_has():
    # What the free tier counts is requests. Forty legs of one chain are one of them.
    now = datetime.now(timezone.utc)
    rows = [leg("tomtom", "c1", now, edge_id=f"e{i}") for i in range(40)]
    assert len({row.chain_id for row in rows}) == 1


def test_spending_is_counted_from_what_was_stored(session):
    now = datetime.now(timezone.utc)
    session.add_all([leg("tomtom", "c1", now), leg("tomtom", "c1", now), leg("tomtom", "c2", now),
                     leg("google", None, now), leg("google", None, now)])
    session.commit()
    assert spent_since(session, "tomtom", now - timedelta(hours=1)) == 2    # two chains
    assert spent_since(session, "google", now - timedelta(hours=1)) == 2    # two single edges


def test_measuring_one_chain_again_costs_another_request(session):
    # The same chain is measured over and over; that is the point. Each pass is its own request and
    # must be charged as one, or a day's spending is undercounted to a single chain's worth.
    first = datetime.now(timezone.utc) - timedelta(hours=2)
    second = datetime.now(timezone.utc)
    session.add_all([leg("tomtom", "c1", first), leg("tomtom", "c1", first),
                     leg("tomtom", "c1", second), leg("tomtom", "c1", second)])
    session.commit()
    assert spent_since(session, "tomtom", first - timedelta(hours=1)) == 2


def test_yesterdays_spending_does_not_count_against_today(session):
    now = datetime.now(timezone.utc)
    session.add(leg("here", "old", now - timedelta(days=2)))
    session.commit()
    assert spent_since(session, "here", now - timedelta(days=1)) == 0
    assert remaining_today(session, "here", now) == BUDGETS["here"].daily_allowance()


def test_a_spent_provider_stops_being_allowed(session):
    now = datetime.now(timezone.utc)
    allowance = BUDGETS["here"].daily_allowance()
    session.add_all([leg("here", f"c{i}", now) for i in range(allowance)])
    session.commit()
    assert remaining_today(session, "here", now) == 0
    assert not may_request(session, "here", now)


def test_a_reserve_is_always_held_back():
    for budget in BUDGETS.values():
        cap = (budget.per_day or 10**9) * budget.accounts
        assert budget.daily_allowance() < cap
