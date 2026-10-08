"""What collection has gathered so far, and what it is still missing."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from travelsmart.db import LegMeasurement, make_session_factory
from travelsmart.measure.budget import BUDGETS, remaining_today
from travelsmart.measure.chains import measurable


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", type=Path, default=Path("data/network/network.json"))
    parser.add_argument("--hours", type=float, default=24)
    args = parser.parse_args()

    network = json.loads(args.network.read_text(encoding="utf-8"))
    targets = {edge_id for edge_id, edge in network["edges"].items() if measurable(edge)}
    since = datetime.now(timezone.utc) - timedelta(hours=args.hours)

    sessions = make_session_factory()
    with sessions() as session:
        rows = session.scalars(select(LegMeasurement).where(LegMeasurement.created_at >= since)).all()
        budgets = {provider: (remaining_today(session, provider), BUDGETS[provider].daily_allowance())
                   for provider in BUDGETS}

    if not rows:
        print(f"nothing measured in the last {args.hours:g} hours", file=sys.stderr)
        return

    print(f"last {args.hours:g} hours: {len(rows):,} legs stored\n")
    print(f"{'provider':<9}{'legs':>9}{'accepted':>10}{'rejected':>10}{'edges':>9}{'budget left':>14}")
    per_provider = defaultdict(list)
    for row in rows:
        per_provider[row.provider].append(row)
    for provider, group in sorted(per_provider.items()):
        accepted = sum(1 for row in group if row.accepted)
        edges = len({row.edge_id for row in group if row.accepted})
        left, allowance = budgets.get(provider, (0, 0))
        print(f"{provider:<9}{len(group):>9,}{accepted:>10,}{len(group) - accepted:>10,}"
              f"{edges:>9,}{f'{left}/{allowance}':>14}")

    covered = {row.edge_id for row in rows if row.accepted} & targets
    print(f"\ncoverage: {len(covered):,} of {len(targets):,} measurable edges "
          f"({len(covered) / len(targets):.1%}) have at least one accepted measurement")

    carried = [row for row in rows if not row.accepted and "shorter than" in (row.rejected_because or "")]
    if carried:
        print(f"{len(carried):,} legs were stretches a chain drove through to reach what it was for, "
              f"too short to judge; they are not failures")
    rejected = [row for row in rows if not row.accepted and row not in carried]
    if rejected:
        kinds = Counter(network["edges"][row.edge_id]["kind"] for row in rejected
                        if row.edge_id in network["edges"])
        print(f"\n{len(rejected):,} genuinely rejected ({len(rejected) / max(len(rows) - len(carried), 1):.0%} of what was judged), by edge kind: {dict(kinds)}")
        reasons = Counter("no distance" if "no distance" in (row.rejected_because or "")
                          else "wrong road" if "route over" in (row.rejected_because or "")
                          else "length" for row in rejected)
        print(f"reasons: {dict(reasons)}")
        worst = Counter(row.edge_id for row in rejected).most_common(5)
        if worst and worst[0][1] > 1:
            print("edges rejected most often (badly placed nodes are fixed in the network, §4):")
            for edge_id, count in worst:
                if count > 1:
                    edge = network["edges"].get(edge_id, {})
                    print(f"  {edge_id} {edge.get('kind', '?')} {edge.get('metres', 0):.0f} m "
                          f"{edge.get('roads', [])}: {count} times")


if __name__ == "__main__":
    main()
