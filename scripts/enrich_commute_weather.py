"""Enrich a completed commute day with Open-Meteo historical weather."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from travelsmart.commute_weather import enrich_day


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="YYYY-MM-DD; defaults to five days ago in Belgium")
    args = parser.parse_args()
    day = date.fromisoformat(args.date) if args.date else datetime.now(ZoneInfo("Europe/Brussels")).date() - timedelta(days=5)
    print(json.dumps(enrich_day(day)))


if __name__ == "__main__":
    main()
