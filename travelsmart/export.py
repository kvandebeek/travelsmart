from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import select
from travelsmart.analytics import best_moments, best_round_trips, grouped_statistics, schedule_slots
from travelsmart.db import Measurement

def export_static(session, settings, output: Path):
    journeys_dir = output / "journeys"; journeys_dir.mkdir(parents=True, exist_ok=True)
    locations = [vars(x) for x in settings.locations.values()]
    journeys = [vars(x) | {"via": list(x.via)} for x in settings.journeys.values()]
    (output / "locations.json").write_text(json.dumps(locations, indent=2), encoding="utf-8")
    (output / "journeys.json").write_text(json.dumps(journeys, indent=2), encoding="utf-8")

    measured = {}  # journeys without data get no detail document; the dashboard shows n/a
    for journey in settings.journeys.values():
        rows = session.scalars(select(Measurement).where(Measurement.journey_id == journey.id)).all()
        if rows:
            measured[journey.id] = (rows, grouped_statistics(rows))

    index = []
    for journey_id, (rows, stats) in measured.items():
        journey = settings.journeys[journey_id]
        moments = best_moments(stats["by_weekday_slot"], settings.sufficient_samples, settings.min_spread)
        reverse = measured.get(f"{journey.destination}_{journey.origin}")
        round_trips = best_round_trips(stats["by_weekday_slot"], reverse[1]["by_weekday_slot"], min_spread=settings.min_spread,
                                       min_samples=settings.sufficient_samples) if reverse else {}
        distance_m = sum(r.distance_m for r in rows) / len(rows)
        doc = {"journey": journey_id, "measurement_type": "baseline", "sufficient_samples": settings.sufficient_samples,
               "reliable_samples": settings.reliable_samples, "distance_m": distance_m, "moments": moments,
               "round_trips": round_trips, "statistics": stats}
        (journeys_dir / f"{journey_id}.json").write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
        index.append({"journey": journey_id, "sufficient_samples": settings.sufficient_samples, "distance_m": distance_m,
                      "samples": stats["overall"]["samples"],
                      **{k: moments[k] for k in ("meaningful", "spread", "covered_cells", "best", "worst")}})
    (output / "index.json").write_text(json.dumps({"generated": datetime.now(timezone.utc).isoformat(), "reliable_samples": settings.reliable_samples,
                                                   "schedule_slots": schedule_slots(settings.schedules), "journeys": index}, indent=2), encoding="utf-8")
