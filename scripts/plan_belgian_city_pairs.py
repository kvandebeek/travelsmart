"""Write every ordered pair of Belgian municipalities without making map requests."""

from __future__ import annotations

import argparse
import csv
from itertools import permutations
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIELDS = ("origin_nis_code", "origin_name", "origin_location",
          "destination_nis_code", "destination_name", "destination_location")


def load_cities(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames or not {"nis_code", "name", "location"}.issubset(reader.fieldnames):
            raise ValueError("city file needs nis_code,name,location columns")
        cities = list(reader)
    if len(cities) < 2 or any(not city["nis_code"] or not city["name"] or not city["location"]
                              for city in cities):
        raise ValueError("city file needs at least two complete rows")
    if len({city["nis_code"] for city in cities}) != len(cities):
        raise ValueError("municipality NIS codes must be unique")
    return cities


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cities-file", type=Path, default=ROOT / "config" / "belgian_municipalities.csv")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "belgian_city_pairs.csv")
    parser.add_argument("--origin-code", help="Limit to routes starting from one municipality NIS code")
    parser.add_argument("--dry-run", action="store_true", help="Print pair count without writing a file")
    args = parser.parse_args()
    try:
        cities = load_cities(args.cities_file)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    if args.origin_code and args.origin_code not in {city["nis_code"] for city in cities}:
        parser.error(f"unknown origin NIS code: {args.origin_code}")
    count = (len(cities) - 1) * (1 if args.origin_code else len(cities))
    if args.dry_run:
        print(f"{len(cities)} municipalities; {count} directed pairs")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        for origin, destination in permutations(cities, 2):
            if args.origin_code and origin["nis_code"] != args.origin_code:
                continue
            writer.writerow({
                "origin_nis_code": origin["nis_code"],
                "origin_name": origin["name"],
                "origin_location": origin["location"],
                "destination_nis_code": destination["nis_code"],
                "destination_name": destination["name"],
                "destination_location": destination["location"],
            })
    print(f"Wrote {count} directed pairs to {args.output}")


if __name__ == "__main__":
    main()
