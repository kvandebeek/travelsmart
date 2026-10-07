"""Inspect the deterministic monthly plan without spending API calls."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

from travelsmart.commute_planner import load_plan_inputs, plan_month


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", help="YYYY-MM (default: current month in Belgium)")
    parser.add_argument("--active-only", action="store_true", help="Only validated, API-ready routes")
    parser.add_argument("--day", help="Show the polls for one YYYY-MM-DD date")
    args = parser.parse_args()
    schedule, routes = load_plan_inputs(active_only=args.active_only)
    month = args.month or datetime.now(ZoneInfo(schedule["timezone"])).strftime("%Y-%m")
    year, number = map(int, month.split("-"))
    polls = plan_month(year, number, schedule, routes)
    summary = {"month": month, "candidate_routes": len(routes), "planned_calls": len(polls),
               "core_calls": sum(poll.tier == "core" for poll in polls),
               "rotating_calls": sum(poll.tier == "rotating" for poll in polls),
               "extra_calls": sum(poll.tier == "extra" for poll in polls),
               "calls_by_account": dict(sorted(Counter(poll.account for poll in polls).items())),
               "distinct_routes": len({poll.route_id for poll in polls}),
               "daily_calls": dict(sorted(Counter(poll.scheduled_at.date().isoformat() for poll in polls).items()))}
    if args.day:
        summary["polls"] = [{"route_id": poll.route_id, "direction": poll.direction,
                             "tier": poll.tier, "account": poll.account, "scheduled_at": poll.scheduled_at.isoformat()}
                            for poll in polls if poll.scheduled_at.date().isoformat() == args.day]
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
