"""Rebuild the published profiles and the browser export, once or on a loop (§7, §9).

Collection writes raw legs all day; the published files are what anyone actually reads. This keeps
them current without having to remember two commands, and both are written through a temporary file,
so a page loading halfway through a refresh still gets a whole file.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STEPS = [("profiles", ["scripts/build_profiles.py"]),
         ("browser export", ["scripts/export_for_browser.py"])]


def refresh() -> bool:
    ok = True
    for name, command in STEPS:
        result = subprocess.run([sys.executable, *command], cwd=ROOT, capture_output=True, text=True)
        tail = (result.stderr or result.stdout).strip().splitlines()
        print(f"{datetime.now():%H:%M:%S} {name}: {tail[-1] if tail else 'no output'}",
              file=sys.stderr, flush=True)
        ok &= result.returncode == 0
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--every", type=float, default=0,
                        help="minutes between refreshes; 0 refreshes once and stops")
    parser.add_argument("--hours", type=float, default=0, help="stop after this long (0 = forever)")
    args = parser.parse_args()

    started = time.monotonic()
    while True:
        refresh()
        if not args.every:
            return 0
        if args.hours and time.monotonic() - started >= args.hours * 3600:
            return 0
        time.sleep(args.every * 60)


if __name__ == "__main__":
    raise SystemExit(main())
