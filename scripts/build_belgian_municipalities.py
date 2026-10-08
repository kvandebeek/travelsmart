"""Build the Belgian municipality list from Statbel's published REFNIS file."""

from __future__ import annotations

import argparse
import csv
import io
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://statbel.fgov.be/sites/default/files/Over_Statbel_FR/Nomenclaturen/REFNIS_2025.csv"
MUNICIPALITY_LANGUAGES = {"N", "F", "FN", "D"}


def build_rows(source_bytes: bytes) -> list[dict]:
    reader = csv.DictReader(io.StringIO(source_bytes.decode("utf-8-sig")), delimiter="|")
    rows = []
    for source in reader:
        language = source["Langue"].strip()
        if language not in MUNICIPALITY_LANGUAGES:
            continue
        code = source["Code INS"].strip()
        french = source["Entités administratives"].strip()
        dutch = source["Administratieve eenheden"].strip()
        name = dutch if language in {"N", "D"} else french
        if len(code) != 5 or not code.isdigit() or not name:
            raise ValueError(f"Invalid municipality row: {code!r}")
        rows.append({"id": code, "nis_code": code, "name": name, "name_fr": french,
                     "name_nl": dutch, "language": language,
                     "location": f"{name}, Belgium"})
    if len({row["nis_code"] for row in rows}) != len(rows):
        raise ValueError("Duplicate municipality NIS code")
    return sorted(rows, key=lambda row: row["nis_code"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="Local REFNIS CSV instead of downloading it")
    parser.add_argument("--output", type=Path, default=ROOT / "config" / "belgian_municipalities.csv")
    args = parser.parse_args()
    if args.source:
        source_bytes = args.source.read_bytes()
    else:
        request = Request(SOURCE_URL, headers={"User-Agent": "TravelSmart/0.1"})
        with urlopen(request, timeout=30) as response:
            source_bytes = response.read()
    rows = build_rows(source_bytes)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=("id", "nis_code", "name", "name_fr",
                                                 "name_nl", "language", "location"))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} municipalities to {args.output}")


if __name__ == "__main__":
    main()
