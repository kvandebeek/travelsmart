"""Measure motorway corridors now (one TomTom call per corridor and direction).

python scripts/run_corridor_tick.py --corridor all --direction forward --execute
The account follows the time of day like the commute slots: morning account before 12:00
Brussels time, evening account after. Keys come from the environment (or a local .env).
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from travelsmart.commute_corridors import NODES_FILE, measure
from travelsmart.config import ROOT


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corridor", default="all")
    parser.add_argument("--direction", choices=("forward", "reverse", "both"), default="both")
    parser.add_argument("--execute", action="store_true", help="Spend TomTom calls")
    args = parser.parse_args()
    env = ROOT / ".env"
    if env.exists():  # local runs; GitHub provides the variables itself
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                name, value = line.split("=", 1)
                os.environ.setdefault(name.strip().upper(), value.strip())
    schedule = yaml.safe_load((ROOT / "config" / "commute_schedule.yaml").read_text(encoding="utf-8"))
    account = schedule["tomtom_accounts"]["morning" if datetime.now(ZoneInfo("Europe/Brussels")).hour < 12 else "evening"]
    corridors = json.loads((ROOT / "config" / NODES_FILE).read_text(encoding="utf-8"))
    chosen = list(corridors) if args.corridor == "all" else [args.corridor]
    directions = ["forward", "reverse"] if args.direction == "both" else [args.direction]
    if not args.execute:
        print(json.dumps({"would_measure": [f"{c}:{d}" for c in chosen for d in directions], "account": account["id"]}))
        return
    key = os.environ.get(account["key_env"])
    if not key:
        print(f"::warning::missing {account['key_env']}")
        return
    for corridor in chosen:
        for direction in directions:
            row = measure(corridor, direction, key, account["id"])
            legs = row.get("legs", [])
            print(json.dumps({"corridor": corridor, "direction": direction, "status": row["status"],
                              "legs": len(legs), "unusable_legs": sum(not leg["usable"] for leg in legs)}))


if __name__ == "__main__":
    main()
