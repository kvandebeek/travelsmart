"""What a provider may still spend, and how to pace it (docs/network-design.md §6.5).

Every provider here is on a free tier with a hard cap, and going over it does not degrade: it stops
answering, or it starts charging. So the budget is checked before a request, not counted after one,
and it is checked against the rows already stored rather than an in-memory tally, which means a
restarted or crashed collector cannot forget what it already spent.

Rates are paced evenly over the day rather than spent as fast as the cap allows. A day's worth of
measurements taken in one morning would describe the morning, not the day.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# A reserve keeps a little of each cap for re-measuring something that failed, and for the days a
# month is longer than the pacing assumed.
RESERVE = 0.1


@dataclass(frozen=True)
class Budget:
    """One provider's free-tier limits, counted in requests rather than legs."""
    provider: str
    per_day: int | None = None
    per_30_days: int | None = None
    accounts: int = 1

    def daily_allowance(self) -> int:
        """How many requests a day this provider may take, after the reserve."""
        limits = []
        if self.per_day is not None:
            limits.append(self.per_day * self.accounts)
        if self.per_30_days is not None:
            limits.append(self.per_30_days * self.accounts / 30)
        if not limits:
            raise ValueError(f"{self.provider} has no limit to pace against")
        return int(min(limits) * (1 - RESERVE))

    def seconds_between_requests(self) -> float:
        """Spacing that spreads the daily allowance evenly over 24 hours."""
        allowance = self.daily_allowance()
        return 86_400 / allowance if allowance else float("inf")


# §6.1: two TomTom accounts share the monthly free tier. §6.3: HERE's cap is the small one.
BUDGETS = {
    "tomtom": Budget("tomtom", per_30_days=20_000, accounts=2),
    "here": Budget("here", per_day=96, per_30_days=3_000),
    # Google is a browser, not an API: it has no published quota, so it is paced by politeness.
    "google": Budget("google", per_day=8_000),
}


def spent_since(session, provider: str, since: datetime) -> int:
    """How many requests this provider has made since a moment, counted from what was stored.

    Rows are per leg, so a chain of 40 legs is one request with 40 rows. All the rows of one request
    share its created_at, so a request is a distinct (chain, created_at): counting distinct chains
    alone would collapse every repeat measurement of the same chain into one, and repeat measurement
    is the entire point. A row without a chain (Google measures a single edge) is one request.
    """
    from sqlalchemy import func, select

    from travelsmart.db import LegMeasurement

    chains = session.scalar(
        select(func.count(func.distinct(
            func.concat(LegMeasurement.chain_id, "@", LegMeasurement.created_at))))
        .where(LegMeasurement.provider == provider, LegMeasurement.created_at >= since,
               LegMeasurement.chain_id.isnot(None))) or 0
    singles = session.scalar(
        select(func.count()).select_from(LegMeasurement)
        .where(LegMeasurement.provider == provider, LegMeasurement.created_at >= since,
               LegMeasurement.chain_id.is_(None))) or 0
    return chains + singles


def remaining_today(session, provider: str, now: datetime | None = None) -> int:
    """How many more requests this provider may make in the next 24 hours."""
    budget = BUDGETS[provider]
    now = now or datetime.now(timezone.utc)
    spent = spent_since(session, provider, now - timedelta(days=1))
    return max(0, budget.daily_allowance() - spent)


def may_request(session, provider: str, now: datetime | None = None) -> bool:
    return remaining_today(session, provider, now) > 0
