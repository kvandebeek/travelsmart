"""Build a reviewable list of candidate home-to-work commutes, without API calls."""

from __future__ import annotations

import csv
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

import yaml

from travelsmart.config import ROOT


def distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = map(radians, a)
    lat2, lon2 = map(radians, b)
    angle = 2 * asin(sqrt(sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2))
    return 6371.0088 * angle


def build_rows(config_dir: Path = ROOT / "config") -> list[dict]:
    catalogue = yaml.safe_load((config_dir / "commute_catalogue.yaml").read_text(encoding="utf-8"))
    locations = yaml.safe_load((config_dir / "locations.yaml").read_text(encoding="utf-8"))["locations"]
    areas = catalogue["areas"]
    if not 0 < catalogue["minimum_distance_km"] <= catalogue["candidate_minimum_straight_line_km"]:
        raise ValueError("Invalid minimum distance settings")

    def reference(area: dict) -> tuple[float, float]:
        if "reference_coordinates" in area:
            return tuple(area["reference_coordinates"])
        location = locations[area["reference"]]
        return location["latitude"], location["longitude"]

    rows: dict[tuple[str, str], dict] = {}

    def add(home_id: str, work_id: str, tier: str, corridor: list[str] | None = None) -> None:
        if home_id not in areas or work_id not in areas or home_id == work_id:
            raise ValueError(f"Invalid commute {home_id} -> {work_id}")
        home, work = areas[home_id], areas[work_id]
        key = home_id, work_id
        distance = round(distance_km(reference(home), reference(work)), 1)
        if tier == "regular" and distance < catalogue["candidate_minimum_straight_line_km"]:
            raise ValueError(f"Regular candidate below distance filter: {key}")
        rows[key] = {
            "id": f"{home_id}__{work_id}",
            "home_area": home_id,
            "home_place": home["home"],
            "work_area": work_id,
            "work_place": work["work"],
            "reference_distance_km": distance,
            "tier": tier,
            "possible_corridor": ";".join(corridor or []),
            "status": "candidate_needs_access_points_and_road_distance",
        }

    for home_id, home in areas.items():
        candidates = []
        for work_id, work in areas.items():
            if work_id == home_id:
                continue
            distance = distance_km(reference(home), reference(work))
            if catalogue["candidate_minimum_straight_line_km"] <= distance <= catalogue["maximum_regular_straight_line_km"]:
                candidates.append((distance, work_id))
        selected = {work_id for _, work_id in sorted(candidates)[: catalogue["nearby_destinations_per_home"]]}
        hub_candidates = [(distance, work_id) for distance, work_id in candidates
                          if work_id in catalogue["regional_hubs"] and work_id not in selected]
        selected.update(work_id for _, work_id in sorted(hub_candidates)[: catalogue["hub_destinations_per_home"]])
        for work_id in sorted(selected):
            add(home_id, work_id, "regular")

    for item in catalogue["long_distance_watchlist"]:
        unknown = set(item.get("corridor", [])) - catalogue["corridor_nodes"].keys()
        if unknown:
            raise ValueError(f"Unknown corridor nodes: {sorted(unknown)}")
        add(item["home"], item["work"], "watchlist", item.get("corridor"))

    return sorted(rows.values(), key=lambda row: (row["home_area"], row["tier"] != "watchlist", row["reference_distance_km"]))


if __name__ == "__main__":
    output = ROOT / "config" / "commute_routes.csv"
    rows = build_rows()
    with output.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} candidate commutes to {output}")
