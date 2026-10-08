"""Place every Belgian municipality and every Google Maps location on the "When is it calm?" map.

Usage: python scripts/build_municipality_points.py --statbel-dir DIR
DIR must contain these Statbel open data files (https://statbel.fgov.be/en/open-data):
  OPENDATA_SECTOREN_2024.txt   population per statistical sector, 1 January 2024
      .../opendata/bevolking/sectoren/OPENDATA_SECTOREN_2024.zip
  sectors_2024.geojson         sh_statbel_statistical_sectors_31370_20240101 (same sector codes as the population)
  sectors_2025.geojson         sh_statbel_statistical_sectors_31370_20250101 (the 565 merged municipalities)
      .../opendata/Statistische%20sectoren/<name>.geojson.zip

Writes, for the dashboard export (so CI needs no Statbel download):
  config/belgian_municipality_points.csv     one main point per 2025 municipality, and the commute
                                             catalogue town it belongs to, if any
  config/google_maps_place_municipalities.csv  the 2025 municipality of every known Google Maps location

A municipality's main point is the population-weighted centre of its most populated former
municipality (deelgemeente), so merged municipalities sit on their main town, as Google places
a town name. 2025 sector codes were renumbered, so 2024 sectors are matched to 2025 municipalities
by location. Rerun after adding Google Maps points or collecting from new point files.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import yaml
from shapely import STRtree
from shapely.geometry import Point, shape

from travelsmart.config import ROOT
from travelsmart.lambert72 import to_wgs84

COORDINATES = re.compile(r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*")
BRUSSELS_REGION = "04000"
ENGLISH_NAMES = {"brussels": "Brussel", "antwerp": "Antwerpen", "ghent": "Gent", "bruges": "Brugge"}
POINT_FILES = ("config/google_maps_points.csv", "config/google_maps_stretch_points.csv",
               "config/belgian_municipalities.csv", "data/google_maps_south_points.csv",
               "data/google_maps_two_points.csv")


def plain(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def municipality_index(path: Path) -> tuple[STRtree, list[str], dict[str, dict]]:
    """2025 sectors as a spatial index of municipality codes, plus each municipality's names."""
    features = json.loads(path.read_text(encoding="utf-8"))["features"]
    shapes, codes, munis = [], [], {}
    for feature in features:
        props = feature["properties"]
        shapes.append(shape(feature["geometry"]))
        codes.append(props["cd_munty_refnis"])
        munis[props["cd_munty_refnis"]] = {"region": props["cd_rgn_refnis"], "names": {
            props[f"tx_munty_descr_{lang}"] for lang in ("nl", "fr", "de") if props.get(f"tx_munty_descr_{lang}")}}
    return STRtree(shapes), codes, munis


def to_lambert(lat: float, lon: float) -> tuple[float, float]:
    """WGS84 to Lambert 72 by Newton steps on to_wgs84 (millimetre agreement after a few steps)."""
    x, y = 150000 + (lon - 4.3675) * 70000, 165000 + (lat - 50.8) * 111000
    for _ in range(8):
        la, lo = to_wgs84(x, y)
        dlat_dx = (to_wgs84(x + 1, y)[0] - la, to_wgs84(x, y + 1)[0] - la)
        dlon_dx = (to_wgs84(x + 1, y)[1] - lo, to_wgs84(x, y + 1)[1] - lo)
        det = dlat_dx[0] * dlon_dx[1] - dlat_dx[1] * dlon_dx[0]
        ea, eo = lat - la, lon - lo
        x += (ea * dlon_dx[1] - eo * dlat_dx[1]) / det
        y += (eo * dlat_dx[0] - ea * dlon_dx[0]) / det
    return x, y


def locate(tree: STRtree, codes: list[str], x: float, y: float) -> str | None:
    hits = tree.query(Point(x, y), predicate="intersects")
    if len(hits):
        return codes[hits[0]]
    nearest = tree.nearest(Point(x, y))  # on a border line or just outside the coastline
    return codes[nearest] if nearest is not None else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--statbel-dir", required=True, type=Path)
    args = parser.parse_args()

    tree, codes, munis = municipality_index(args.statbel_dir / "sectors_2025.geojson")
    population = {}
    for line in (args.statbel_dir / "OPENDATA_SECTOREN_2024.txt").read_text(encoding="utf-8-sig").splitlines()[1:]:
        fields = line.split("|")
        population[fields[1]] = int(fields[2])

    # 2024 sectors -> 2025 municipality; collect population-weighted points per former municipality.
    groups: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    aliases: dict[str, set[str]] = defaultdict(set)  # plain name -> 2025 codes
    for feature in json.loads((args.statbel_dir / "sectors_2024.geojson").read_text(encoding="utf-8"))["features"]:
        props = feature["properties"]
        point = shape(feature["geometry"]).representative_point()
        code = locate(tree, codes, point.x, point.y)
        people = population.get(props["cd_sector"], 0)
        groups[code][props["cd_sub_munty"]].append((point.x, point.y, people))
        for lang in ("nl", "fr", "de"):
            if props.get(f"tx_munty_descr_{lang}"):
                aliases[plain(props[f"tx_munty_descr_{lang}"])].add(code)

    with (ROOT / "config" / "belgian_municipalities.csv").open(encoding="utf-8", newline="") as file:
        listed = {row["nis_code"]: row for row in csv.DictReader(file)}
    missing = set(listed) ^ set(munis)
    if missing:
        raise SystemExit(f"municipality lists differ (rerun build_belgian_municipalities.py?): {sorted(missing)}")
    for code, info in munis.items():
        for name in info["names"] | {listed[code]["name"], listed[code]["name_nl"], listed[code]["name_fr"]}:
            aliases[plain(name)] = {code}  # 2025 names win over former municipality names

    points = {}
    for code, subs in groups.items():
        main = max(subs.values(), key=lambda items: sum(p for _, _, p in items))
        weights = [p for _, _, p in main] if any(p for _, _, p in main) else [1] * len(main)
        x = sum(w * px for w, (px, _, _) in zip(weights, main)) / sum(weights)
        y = sum(w * py for w, (_, py, _) in zip(weights, main)) / sum(weights)
        points[code] = to_wgs84(x, y)

    # Commute catalogue towns: the municipality holding most of their anchors (Brussels: the whole region).
    catalogue = yaml.safe_load((ROOT / "config" / "commute_catalogue.yaml").read_text(encoding="utf-8"))
    anchors = yaml.safe_load((ROOT / "config" / "commute_anchors.yaml").read_text(encoding="utf-8"))
    area_of: dict[str, str] = {}
    for area, info in catalogue["areas"].items():
        if info["region"] == "corridor":
            continue
        if info["region"] == "brussels":
            for code, muni in munis.items():
                if muni["region"] == BRUSSELS_REGION:
                    area_of[code] = area
            continue
        votes = Counter(locate(tree, codes, *to_lambert(*point))
                        for kind in ("home", "work") for key, point in anchors[kind].items()
                        if key.split(".")[0] == area)
        code = votes.most_common(1)[0][0]
        if code in area_of:
            raise SystemExit(f"{area} and {area_of[code]} fall in the same municipality {code}")
        area_of[code] = area

    with (ROOT / "config" / "belgian_municipality_points.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file, lineterminator="\n")
        writer.writerow(["nis_code", "name", "lat", "lon", "map_area"])
        for code in sorted(points):
            lat, lon = points[code]
            writer.writerow([code, listed[code]["name"], f"{lat:.5f}", f"{lon:.5f}", area_of.get(code, "")])

    # Every Google Maps location we know of: point files plus everything already collected.
    locations = set()
    for name in POINT_FILES:
        path = ROOT / name
        if path.exists():
            with path.open(encoding="utf-8-sig", newline="") as file:
                locations |= {row["location"].strip() for row in csv.DictReader(file) if row.get("location")}
    observations = ROOT / "observations" / "google_maps" / "captures.jsonl"
    if observations.exists():
        for line in observations.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                locations |= {row["origin_location"], row["destination_location"]}
    resolved, unresolved = {}, []
    for location in sorted(locations):
        match = COORDINATES.fullmatch(location)
        if match:
            resolved[location] = locate(tree, codes, *to_lambert(float(match[1]), float(match[2])))
            continue
        # "Town, Belgium", or "Village, Municipality, Belgium": try the whole name, then each part from the end.
        name = re.sub(r",\s*belgi(?:um|e|que)\s*$", "", location, flags=re.IGNORECASE)
        for candidate in [name, *reversed(name.split(","))]:
            key = plain(ENGLISH_NAMES.get(plain(candidate), candidate))
            if len(aliases.get(key, ())) == 1:
                resolved[location] = next(iter(aliases[key]))
                break
        else:
            unresolved.append(location)
    with (ROOT / "config" / "google_maps_place_municipalities.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file, lineterminator="\n")
        writer.writerow(["location", "nis_code"])
        writer.writerows(sorted(resolved.items()))
    print(json.dumps({"municipalities": len(points), "catalogue_towns": len(area_of),
                      "google_locations": len(resolved), "unresolved": unresolved}, ensure_ascii=False))


if __name__ == "__main__":
    main()
