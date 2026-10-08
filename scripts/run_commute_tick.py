"""Run one due live slot. Defaults to a safe dry run."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from travelsmart.commute_live import run_tick

LABELS = {"regular": "Regular slot", "extra": "Extra stream", "sample": "Manual sample"}


def summary(line: str) -> None:
    """One line on the run's GitHub page (job summary); ignored outside GitHub Actions."""
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as file:
            file.write(line + "\n\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=("tomtom", "here"), default="tomtom")
    parser.add_argument("--execute", action="store_true", help="Spend API calls for verified routes")
    parser.add_argument("--stream", choices=("regular", "extra", "sample"), default="regular",
                        help="regular departure slots, the extra route streams, or a manual sample now")
    parser.add_argument("--count", type=int, default=10, help="routes in a manual sample")
    parser.add_argument("--only-if-idle", action="store_true",
                        help="sample only when no regular slot or extra tick is due right now")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    clock = now.astimezone(ZoneInfo("Europe/Brussels")).strftime("%H:%M")
    if args.stream == "sample" and args.only_if_idle:
        busy = run_tick(now)["due"] + run_tick(now, stream="extra")["due"]
        if busy:
            print(json.dumps({"skipped": "a scheduled slot or tick is due; it was measured instead"}))
            return
    result = run_tick(now, provider=args.provider, execute=args.execute, stream=args.stream, count=args.count)
    print(json.dumps(result))
    label = f"{LABELS[args.stream]}{' (HERE)' if args.provider == 'here' else ''} at {clock}"
    if result["reason"] == "missing_keys":
        # GitHub Actions annotation: visible on the run without failing it.
        print(f"::warning::Skipped {result['due']} due {args.provider} polls; missing secrets: "
              f"{', '.join(result['missing_keys'])}")
        summary(f"⚠️ {label}: {result['due']} due, skipped (missing {', '.join(result['missing_keys'])})")
    elif result["due"] == 0 or result["reason"] in ("nothing_due", "here_disabled", "here_regular_only"):
        summary(f"⏸️ {label}: nothing scheduled now ({result['reason'].replace('_', ' ')})")
    else:
        summary(f"✅ {label}: {result['stored']} of {result['attempted']} measured "
                f"({result['due']} due{', rest already done' if result['attempted'] < result['due'] else ''})")


if __name__ == "__main__":
    main()
