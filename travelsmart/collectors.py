from __future__ import annotations
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from travelsmart.db import Measurement

DEFAULT_WORKERS = 8

def default_workers() -> int:
    return max(1, int(os.getenv("TRAVELSMART_COLLECT_WORKERS", DEFAULT_WORKERS)))

def fetch_route(settings, provider, journey_id: str):
    """Network part of a collection: safe to run concurrently (no database access)."""
    journey = settings.journeys[journey_id]
    origin, destination = settings.locations[journey.origin], settings.locations[journey.destination]
    via = [settings.locations[v] for v in journey.via]
    return provider.calculate_route(origin, destination, via=via)

def build_measurement(settings, provider, journey_id: str, result, now) -> Measurement:
    local = now.astimezone(ZoneInfo(settings.schedules.get("timezone", "Europe/Brussels")))
    return Measurement(journey_id=journey_id, timestamp_utc=now, timestamp_local=local, weekday=local.weekday(),
        departure_time=local.strftime("%H:%M"), provider=provider.name, distance_m=result.distance_m,
        duration_seconds=result.duration_seconds, route_geometry=result.geometry, measurement_type="baseline", created_at=now)

def collect_journey(session, settings, provider, journey_id: str):
    result = fetch_route(settings, provider, journey_id)
    row = build_measurement(settings, provider, journey_id, result, datetime.now(timezone.utc))
    session.add(row); session.commit(); return row

def collect_many(session, settings, provider, journey_ids, workers: int | None = None):
    """Collect many journeys concurrently.

    Routes are fetched by a thread pool (the slow, I/O-bound part); all rows are written by the calling
    thread in one commit, so SQLite never sees concurrent writers. Every row of a batch shares one
    timestamp, which keeps a whole tick in the same departure slot. Returns (stored_rows, failed_ids).
    """
    journey_ids = list(journey_ids)
    now = datetime.now(timezone.utc)
    rows, failed = [], []
    with ThreadPoolExecutor(max_workers=workers or default_workers()) as pool:
        futures = {pool.submit(fetch_route, settings, provider, jid): jid for jid in journey_ids}
        for future in as_completed(futures):
            jid = futures[future]
            try:
                rows.append(build_measurement(settings, provider, jid, future.result(), now))
            except Exception:
                logging.exception("collection failed: %s", jid); failed.append(jid)
    session.add_all(rows); session.commit()
    return rows, failed
