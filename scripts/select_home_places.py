"""Pick residential start places per commute town from Statbel population per statistical sector.

Usage: python scripts/select_home_places.py --statbel-dir DIR
DIR must contain OPENDATA_SECTOREN_2024.txt (population per sector) and sectors.geojson
(sh_statbel_statistical_sectors_31370_20240101). Both are Statbel open data:
https://statbel.fgov.be/sites/default/files/files/opendata/bevolking/sectoren/OPENDATA_SECTOREN_2024.zip
https://statbel.fgov.be/sites/default/files/files/opendata/Statistische%20sectoren/sh_statbel_statistical_sectors_31370_20240101.geojson.zip

Writes config/commute_homes.yaml for review. Places are spread over a town's former
municipalities (deelgemeenten; for Brussels: its 19 municipalities), most populated first,
and placed in the most populated statistical sector of each. Coordinates are a point inside
that sector, not yet snapped to a road (scripts/build_commute_catalogue.py does that).
"""

from __future__ import annotations

import argparse
import json
from math import hypot
from pathlib import Path

import yaml
from shapely.geometry import shape

from travelsmart.config import ROOT
from travelsmart.lambert72 import to_wgs84

# Municipality names as in the 2024 Statbel files (before the 2025 mergers).
TOWNS = {
    "antwerp": ["Antwerpen"], "mechelen": ["Mechelen"], "turnhout": ["Turnhout"], "geel": ["Geel"],
    "mol": ["Mol"], "diepenbeek": ["Diepenbeek"], "hasselt": ["Hasselt"], "genk": ["Genk"],
    "beringen": ["Beringen"], "lommel": ["Lommel"], "maasmechelen": ["Maasmechelen"], "kinrooi": ["Kinrooi"],
    "hamont_achel": ["Hamont-Achel"], "maaseik": ["Maaseik"], "bilzen": ["Bilzen", "Hoeselt"],
    "ghent": ["Gent"], "aalst": ["Aalst"], "sint_niklaas": ["Sint-Niklaas"], "oostende": ["Oostende"],
    "bruges": ["Brugge"], "kortrijk": ["Kortrijk"], "roeselare": ["Roeselare"], "ieper": ["Ieper"],
    "leuven": ["Leuven"], "vilvoorde": ["Vilvoorde"], "halle": ["Halle"], "brussels": "region:04000",
    "liege": ["Liège"], "namur": ["Namur"], "charleroi": ["Charleroi"], "mons": ["Mons"],
    "tournai": ["Tournai"], "wavre": ["Wavre"],
}
FRENCH_NAMES = {"brussels", "liege", "namur", "charleroi", "mons", "tournai", "wavre"}
SMALL_WORDS = {"de", "den", "het", "ter", "la", "le", "les", "du", "des", "en", "van", "sur", "lez", "aux"}


def places_for(population: int, area: str) -> tuple[int, float]:
    """(number of start places, minimum spacing in km) by town size."""
    if area == "brussels":
        return 5, 2.5
    if population >= 150_000:
        return 4, 2.5
    if population >= 60_000:
        return 3, 2.0
    return 2, 1.5


def nice(name: str) -> str:
    words = []
    for index, word in enumerate(name.lower().replace("  ", " ").split(" ")):
        parts = [part if (part in SMALL_WORDS and (index or n)) else part[:1].upper() + part[1:]
                 for n, part in enumerate(word.split("-"))]
        words.append("-".join(parts))
    return " ".join(words)


def clean(name: str) -> str:
    """Drop Statbel annotations: '+ Deel van ...', '& Deel ...', '(...)' and '*'."""
    import re
    name = re.split(r"\s[+&]\s|\(", name)[0]
    return nice(name.replace("*", "").strip(" -"))


def plain(name: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()


def slug(name: str) -> str:
    import unicodedata
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return "_".join(filter(None, "".join(ch if ch.isalnum() else " " for ch in plain).split()))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--statbel-dir", required=True, type=Path)
    args = parser.parse_args()
    population = {}
    for line in (args.statbel_dir / "OPENDATA_SECTOREN_2024.txt").read_text(encoding="utf-8-sig").splitlines()[1:]:
        fields = line.split("|")
        population[fields[1]] = int(fields[2])
    features = json.loads((args.statbel_dir / "sectors.geojson").read_text(encoding="utf-8"))["features"]
    output = {}
    for area, towns in TOWNS.items():
        def member(props):
            if isinstance(towns, str):
                return props["cd_rgn_refnis"] == towns.split(":")[1]
            return props["tx_munty_descr_nl"] in towns or props["tx_munty_descr_fr"] in towns
        sectors = []
        for feature in features:
            props = feature["properties"]
            if not member(props) or population.get(props["cd_sector"], 0) <= 0:
                continue
            point = shape(feature["geometry"]).representative_point()
            french = area in FRENCH_NAMES
            sectors.append({
                "code": props["cd_sector"], "population": population[props["cd_sector"]], "xy": (point.x, point.y),
                "group": props["cd_munty_refnis"] if area == "brussels" else props["cd_sub_munty"],
                "group_name": props["tx_munty_descr_fr" if french else "tx_munty_descr_nl"] if area == "brussels"
                else props["tx_sub_munty_fr" if french else "tx_sub_munty_nl"],
                "sector_name": props["tx_sector_descr_fr" if french else "tx_sector_descr_nl"],
                "municipality": props["tx_munty_descr_fr" if french else "tx_munty_descr_nl"]})
        total = sum(sector["population"] for sector in sectors)
        count, spacing = places_for(total, area)
        groups = {}
        for sector in sectors:
            groups.setdefault(sector["group"], []).append(sector)
        ranked_groups = sorted(groups.values(), key=lambda items: -sum(s["population"] for s in items))
        chosen = []

        def far_enough(sector):
            return all(hypot(sector["xy"][0] - other["xy"][0], sector["xy"][1] - other["xy"][1]) >= spacing * 1000
                       for other in chosen)

        for items in ranked_groups:  # one place per former municipality first
            if len(chosen) == count:
                break
            best = next((s for s in sorted(items, key=lambda s: -s["population"]) if far_enough(s)), None)
            if best:
                best = {**best, "group_population": sum(s["population"] for s in items)}
                chosen.append(best)
        for sector in sorted(sectors, key=lambda s: -s["population"]):  # then fill by spacing
            if len(chosen) == count:
                break
            if sector not in chosen and far_enough(sector):
                chosen.append(sector)
        homes = []
        for sector in chosen:
            group = clean(sector["group_name"])
            group_differs = plain(group) != plain(clean(sector["municipality"]))
            repeated = sum(other["group"] == sector["group"] for other in chosen) > 1
            if area == "brussels":
                name = group  # Brussels: one place per municipality, named after it
            else:
                name = group if group_differs and not repeated else clean(sector["sector_name"])
            lat, lon = to_wgs84(*sector["xy"])
            homes.append({"id": f"{area}.{slug(name)}", "name": name, "sector": sector["code"],
                          "sector_name": clean(sector["sector_name"]), "sector_population": sector["population"],
                          "point": [round(lat, 6), round(lon, 6)]})
        output[area] = {"town_population": total, "homes": homes}
        print(f"{area:14} {total:>9,}  " + ", ".join(f"{h['name']} ({h['sector_population']})" for h in homes))
    header = ("# Residential start places, generated by scripts/select_home_places.py from Statbel open data\n"
              "# (population per statistical sector, 1 January 2024). Review before regenerating the catalogue.\n")
    (ROOT / "config" / "commute_homes.yaml").write_text(
        header + yaml.safe_dump(output, allow_unicode=True, sort_keys=False, width=200), encoding="utf-8")


if __name__ == "__main__":
    main()
