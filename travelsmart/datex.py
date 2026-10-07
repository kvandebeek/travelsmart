"""Flemish Traffic Center DATEX II v3 events: jams, accidents, obstructions, roadworks and lane closures.

Feed (CC-BY, no key): the full list of CURRENT situation records, republished every minute (~700 KB, served
uncompressed). A record carries no end time while it lasts, so a record that drops out of the feed has ended.
Jams (AbnormalTraffic) are re-versioned every minute with a new queue length and geometry: every poll stores one
observation per jam. Other records are kept as one row each, holding their latest version.

Geometry is a GML line in Belgian Lambert 72 (converted to WGS84) or a WGS84 point. The jam line is the full
queue (its length matches queueLength), but its point order is not consistent, so the ends are not labelled
head and tail.
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select

from travelsmart.db import JamObservation, SourceState, TrafficEvent
from travelsmart.lambert72 import to_wgs84

MAX_AGE = timedelta(minutes=15)  # an older publication means the feed is frozen: nothing is updated or closed
XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"
KINDS = {"AbnormalTraffic": "jam", "Accident": "accident", "GeneralObstruction": "obstruction",
         "VehicleObstruction": "obstruction", "AnimalPresenceObstruction": "obstruction", "EnvironmentalObstruction": "obstruction",
         "InfrastructureDamageObstruction": "obstruction", "MaintenanceWorks": "roadworks", "ConstructionWorks": "roadworks",
         "RoadOrCarriagewayOrLaneManagement": "lane_management", "SpeedManagement": "lane_management"}
STATE_KEY = "datex_publication"


@dataclass
class Snapshot:
    published: datetime
    records: list[dict]


def as_utc(moment: datetime | None) -> datetime | None:
    """SQLite returns stored datetimes without their offset; everything is stored in UTC, so naive means UTC."""
    if moment is None:
        return None
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)

def _time(text: str | None) -> datetime | None:
    return as_utc(datetime.fromisoformat(text)) if text else None

def _int(text: str | None) -> int | None:
    try:
        return int(float(text)) if text and text.strip() else None
    except ValueError:
        return None

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]

def geometry(record: ET.Element) -> list[tuple[float, float]]:
    """WGS84 (lat, lon) points of a record: its GML line (Lambert 72 or WGS84) or else its coordinate point."""
    line = record.find(".//{*}gmlLineString")
    values = (line.findtext("{*}posList") or "").split() if line is not None else []
    if values:
        pairs = list(zip(map(float, values[0::2]), map(float, values[1::2])))
        srs = line.get("srsName", "")
        if srs.endswith("31370"):
            return [tuple(round(v, 6) for v in to_wgs84(x, y)) for x, y in pairs]
        if srs.endswith("4326"):
            return pairs  # GML with EPSG:4326 lists latitude first
        return []         # unknown reference system: better no position than a wrong one
    point = record.find(".//{*}pointByCoordinates/{*}pointCoordinates")
    if point is not None and point.findtext("{*}latitude") and point.findtext("{*}longitude"):
        return [(float(point.findtext("{*}latitude")), float(point.findtext("{*}longitude")))]
    return []

def parse(xml: bytes) -> Snapshot:
    root = ET.fromstring(xml)
    published = _time(root.findtext("{*}publicationTime"))
    if published is None:
        raise ValueError("DATEX II feed without publicationTime")
    records = []
    for situation in root.findall("{*}situation"):
        for record in situation.findall("{*}situationRecord"):
            record_type = record.get(XSI_TYPE, "").rsplit(":", 1)[-1]
            linear = record.find(".//{*}alertCLinear")
            alert_c = linear if linear is not None else record.find(".//{*}alertCPoint")
            subtypes = [child.text for child in record if _local(child.tag).endswith("Type") and child.text]
            records.append({
                "record_id": record.get("id"), "situation_id": situation.get("id"), "record_type": record_type,
                "kind": KINDS.get(record_type, record_type[:1].lower() + record_type[1:]),
                "subtype": ",".join(subtypes) or None,
                "version": _int(record.get("version")) or 0,
                "version_time_utc": _time(record.findtext("{*}situationRecordVersionTime")),
                "validity_status": record.findtext("{*}validity/{*}validityStatus"),
                "start_utc": _time(record.findtext("{*}validity/{*}validityTimeSpecification/{*}overallStartTime")),
                "planned_end_utc": _time(record.findtext("{*}validity/{*}validityTimeSpecification/{*}overallEndTime")),
                "direction": alert_c.findtext(".//{*}alertCDirectionCoded") if alert_c is not None else None,
                "primary_location": _int(alert_c.findtext("{*}alertCMethod4PrimaryPointLocation/{*}alertCLocation/{*}specificLocation")) if alert_c is not None else None,
                "secondary_location": _int(alert_c.findtext("{*}alertCMethod4SecondaryPointLocation/{*}alertCLocation/{*}specificLocation")) if alert_c is not None else None,
                "queue_length_m": _int(record.findtext("{*}queueLength")),
                "geometry": geometry(record)})
    return Snapshot(published, records)

def _set_geometry(event: TrafficEvent, points: list[tuple[float, float]]) -> None:
    event.geometry = json.dumps([[round(lat, 6), round(lon, 6)] for lat, lon in points], separators=(",", ":"))
    event.min_lat, event.max_lat = min(p[0] for p in points), max(p[0] for p in points)
    event.min_lon, event.max_lon = min(p[1] for p in points), max(p[1] for p in points)

def ingest(session, snapshot: Snapshot | bytes, now: datetime | None = None) -> dict:
    """Store one poll: upsert every record, add one observation per jam, close records that left the feed."""
    if isinstance(snapshot, bytes):
        snapshot = parse(snapshot)
    now = now or datetime.now(timezone.utc)
    published = snapshot.published
    counters = {"status": "stored", "records": len(snapshot.records), "jams": 0, "new": 0, "updated": 0, "ended": 0, "reopened": 0}
    state = session.get(SourceState, STATE_KEY)
    if state is not None and _time(state.value) >= published:
        return counters | {"status": "duplicate"}
    if now - published > MAX_AGE:
        return counters | {"status": "stale"}   # a frozen feed would otherwise keep ended events alive
    if not snapshot.records:
        # Hundreds of roadworks are always listed; an empty feed is a publisher fault, not "all events ended".
        raise ValueError("DATEX II feed lists no situation records")

    ids = {r["record_id"] for r in snapshot.records}
    known = {e.record_id: e for e in session.scalars(select(TrafficEvent).where(or_(TrafficEvent.record_id.in_(ids), TrafficEvent.gone_utc.is_(None))))}
    handled = set()
    for record in snapshot.records:
        if record["record_id"] in handled:
            continue  # a record listed twice in one publication is stored once
        handled.add(record["record_id"])
        event = known.get(record["record_id"])
        if event is None:
            event = TrafficEvent(record_id=record["record_id"], first_seen_utc=published)
            session.add(event); known[event.record_id] = event; counters["new"] += 1
        elif event.gone_utc is not None:
            event.gone_utc = None; counters["reopened"] += 1
        elif event.version != record["version"]:
            counters["updated"] += 1
        for field in ("situation_id", "kind", "record_type", "subtype", "validity_status", "start_utc", "planned_end_utc",
                      "version", "version_time_utc", "direction", "primary_location", "secondary_location", "queue_length_m"):
            setattr(event, field, record[field])
        event.last_seen_utc = published
        points, queue = record["geometry"], record["queue_length_m"]
        if record["kind"] == "jam":
            counters["jams"] += 1
            if queue is not None and (event.max_queue_length_m is None or queue >= event.max_queue_length_m):
                event.max_queue_length_m = queue
                if points:
                    _set_geometry(event, points)  # keep the jam at its longest: that is the stretch it affected
            elif points and event.geometry is None:
                _set_geometry(event, points)
            ends = (points[0], points[-1]) if points else ((None, None), (None, None))
            session.add(JamObservation(record_id=event.record_id, observed_utc=published, version=record["version"], queue_length_m=queue,
                                       end1_lat=ends[0][0], end1_lon=ends[0][1], end2_lat=ends[1][0], end2_lon=ends[1][1]))
        elif points:
            _set_geometry(event, points)

    for record_id, event in known.items():
        if record_id not in ids and event.gone_utc is None:
            event.gone_utc = published; counters["ended"] += 1
    session.merge(SourceState(key=STATE_KEY, value=published.isoformat()))
    session.commit()
    return counters
