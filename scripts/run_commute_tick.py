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
    parser.add_argument("--stream", choices=("regular", "extra"), default="regular",
                        help="regular departure slots, or the 5-minute extra route stream")
    args = parser.parse_args()
    result = run_tick(datetime.now(timezone.utc), provider=args.provider, execute=args.execute, stream=args.stream)
    print(json.dumps(result))
    if result["reason"] == "missing_keys":
        # GitHub Actions annotation: visible on the run without failing it.
        print(f"::warning::Skipped {result['due']} due {args.provider} polls; missing secrets: "
              f"{', '.join(result['missing_keys'])}")


if __name__ == "__main__":
    main()
