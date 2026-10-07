from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent

@dataclass(frozen=True)
class Location:
    id: str
    display_name: str
    municipality: str
    country: str
    latitude: float
    longitude: float
    province: str = ""

@dataclass(frozen=True)
class Journey:
    id: str
    origin: str
    destination: str
    mode: str = "driving"
    active: bool = True
    via: tuple[str, ...] = ()

@dataclass(frozen=True)
class Settings:
    locations: dict[str, Location]
    journeys: dict[str, Journey]
    schedules: dict
    sufficient_samples: int = 1
    reliable_samples: int = 20
    min_spread: float = 0.03

def load_settings(config_dir: Path | None = None) -> Settings:
    config_dir = config_dir or ROOT / "config"
    with (config_dir / "locations.yaml").open(encoding="utf-8") as f:
        raw_locations = yaml.safe_load(f)["locations"]
    locations = {key: Location(id=key, **value) for key, value in raw_locations.items()}
    with (config_dir / "journeys.yaml").open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    journey_items = raw.get("journeys", [])
    # Compact matrix notation: one journey for every ordered pair of listed locations.
    pair_ids = raw.get("all_pairs", [])
    if pair_ids == "all":
        pair_ids = list(locations)
    for origin in pair_ids:
        for destination in pair_ids:
            if origin != destination:
                journey_items.append({"id": f"{origin}_{destination}", "origin": origin,
                                      "destination": destination, "mode": "driving", "active": True})
    journeys = {item["id"]: Journey(**item) for item in journey_items}
    for journey in journeys.values():
        if journey.origin not in locations or journey.destination not in locations:
            raise ValueError(f"Journey {journey.id} references an unknown location")
    return Settings(locations, journeys, raw.get("schedules", {}), raw.get("sufficient_samples", 1),
                    raw.get("reliable_samples", 20), raw.get("min_spread", 0.03))
