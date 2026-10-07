"""Run one due live slot. Defaults to a safe dry run."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from travelsmart.commute_live import run_tick


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=("tomtom", "here"), default="tomtom")
    parser.add_argument("--execute", action="store_true", help="Spend API calls for verified routes")
    args = parser.parse_args()
    print(json.dumps(run_tick(datetime.now(timezone.utc), provider=args.provider, execute=args.execute)))


if __name__ == "__main__":
    main()
