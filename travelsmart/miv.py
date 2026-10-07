"""Flemish loop-detector feed (MIV): parse, filter and fold readings into running statistics.

Feed (open data, Flemish Traffic Center): one <meetpunt> per lane with five vehicle classes, each with a count
per minute and an arithmetic and a harmonic average speed in km/h. Speed 252 with count 0 is filler for "no data".
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from travelsmart.analytics import slot_label
from travelsmart.db import MivLane, MivSite, SiteLatest, SourceState, SpeedCell

MAX_AGE = timedelta(minutes=10)   # readings older than this (relative to the feed's publication time) are stale
MIN_KMH, MAX_KMH = 5.0, 200.0     # outside this range a speed is not a measurement
LOCAL = ZoneInfo("Europe/Brussels")


@dataclass
class Snapshot:
    """One parsed poll of the data feed (the document is ~10 MB, so it is parsed exactly once per poll)."""
    published: datetime
    config_time: str              # the feed repeats the configuration's change time: re-download the config only when it changes
    lanes: list[dict]


def site_key(road_segment: str, km_marker: str) -> str:
    """Lanes of one carriageway at one km marker form a site, e.g. "A0010001@10,371"."""
    return f"{road_segment}@{km_marker}"

def _number(text: str | None) -> float | None:
    """The configuration uses decimal commas ("50,9828171")."""
    try:
        return float(text.replace(",", ".")) if text else None
    except ValueError:
        return None

def _as_utc(moment: datetime) -> datetime:
    """SQLite returns stored datetimes without their offset; everything is stored in UTC, so naive means UTC."""
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)

def parse_config(xml: bytes) -> tuple[str, dict[str, str], dict[str, dict]]:
    """Return (config change time, unieke_id -> site key, site key -> metadata with the mean lane position)."""
    root = ET.fromstring(xml)
    lane_sites: dict[str, str] = {}
    grouped: dict[str, list[dict]] = defaultdict(list)
    for lane in root.findall("meetpunt"):
        road = lane.findtext("Ident_8") or ""
        # lanes without road information cannot be grouped; each one stands alone
        key = site_key(road, lane.findtext("Kmp_Rsys") or "") if road not in ("", "NULL") else f"lane@{lane.get('unieke_id')}"
        lane_sites[lane.get("unieke_id")] = key
        grouped[key].append({"name": lane.findtext("volledige_naam") or "", "road": road,
                             "lat": _number(lane.findtext("breedtegraad_EPSG_4326")), "lon": _number(lane.findtext("lengtegraad_EPSG_4326"))})
    sites = {}
    for key, items in grouped.items():
        located = [i for i in items if i["lat"] is not None and i["lon"] is not None]
        if not located:
            continue  # a site without a position cannot be matched to routes
        sites[key] = {"name": items[0]["name"], "road": items[0]["road"], "lanes": len(items),
                      "lat": sum(i["lat"] for i in located) / len(located), "lon": sum(i["lon"] for i in located) / len(located)}
    return root.findtext("tijd_laatste_config_wijziging") or "", {u: k for u, k in lane_sites.items() if k in sites}, sites

def parse_data(xml: bytes) -> Snapshot:
    root = ET.fromstring(xml)
    lanes = []
    for lane in root.findall("meetpunt"):
        lanes.append({
            "uid": lane.get("unieke_id"),
            "observed": datetime.fromisoformat(lane.findtext("tijd_waarneming")),
            "usable": lane.findtext("beschikbaar") == "1" and lane.findtext("defect") == "0" and lane.findtext("geldig") == "1",
            "classes": [(int(c.findtext("verkeersintensiteit") or 0), float(c.findtext("voertuigsnelheid_harmonisch") or 0))
                        for c in lane.findall("meetdata")]})
    return Snapshot(datetime.fromisoformat(root.findtext("tijd_publicatie")), root.findtext("tijd_laatste_config_wijziging") or "", lanes)

def combine(lanes: list[dict]) -> tuple[float, int] | None:
    """Flow-weighted harmonic mean speed (km/h) and vehicle count over lanes and classes, or None without traffic."""
    flow = hours = 0.0
    for lane in lanes:
        for count, kmh in lane["classes"]:
            if count > 0 and MIN_KMH <= kmh <= MAX_KMH:
                flow += count; hours += count / kmh   # count / speed = vehicle-hours per km
    return (flow / hours, int(flow)) if flow else None

def fold(cell: SpeedCell, seconds_per_km: float, weight: float) -> None:
    """Weighted Welford update: fold one reading into the cell's running statistics."""
    first = cell.readings == 0
    total = cell.weight + weight
    delta = seconds_per_km - cell.mean_spk
    cell.mean_spk += delta * weight / total
    cell.m2 += weight * delta * (seconds_per_km - cell.mean_spk)
    cell.weight, cell.readings = total, cell.readings + 1
    cell.min_spk = seconds_per_km if first else min(cell.min_spk, seconds_per_km)
    cell.max_spk = seconds_per_km if first else max(cell.max_spk, seconds_per_km)

def sync_sites(session, config_xml: bytes) -> int:
    changed, lane_sites, sites = parse_config(config_xml)
    for sid, info in sites.items():
        session.merge(MivSite(site_id=sid, name=info["name"], road=info["road"], latitude=info["lat"], longitude=info["lon"], lanes=info["lanes"]))
    for uid, key in lane_sites.items():
        session.merge(MivLane(unieke_id=uid, site_id=key))
    session.merge(SourceState(key="miv_config_time", value=changed))
    session.commit()
    return len(sites)

def config_is_current(session, config_time: str) -> bool:
    state = session.get(SourceState, "miv_config_time")
    return state is not None and state.value == config_time

def ingest(session, snapshot: Snapshot | bytes, lane_sites: dict[str, str] | None = None) -> dict:
    """Fold the fresh, valid readings of one poll into the speed cells. Returns counters for logging."""
    if isinstance(snapshot, bytes):
        snapshot = parse_data(snapshot)
    if lane_sites is None:
        lane_sites = {row.unieke_id: row.site_id for row in session.scalars(select(MivLane))}
    counters = {"sites": 0, "unmapped": 0, "stale": 0, "invalid": 0, "no_traffic": 0, "duplicate": 0, "stored": 0}
    by_site: dict[str, list[dict]] = defaultdict(list)
    for lane in snapshot.lanes:
        if lane["uid"] in lane_sites:
            by_site[lane_sites[lane["uid"]]].append(lane)
        else:
            counters["unmapped"] += 1
    counters["sites"] = len(by_site)

    candidates = []  # (site, observed UTC, kmh, flow)
    for sid, site_lanes in by_site.items():
        fresh = [l for l in site_lanes if snapshot.published - l["observed"] <= MAX_AGE]
        if not fresh:
            counters["stale"] += 1; continue
        good = [l for l in fresh if l["usable"]]
        if not good:
            counters["invalid"] += 1; continue
        combined = combine(good)
        if combined is None:
            counters["no_traffic"] += 1; continue
        candidates.append((sid, _as_utc(max(l["observed"] for l in good)), *combined))
    if not candidates:
        return counters

    # Compare and store in UTC: the feed mixes +01:00 and +02:00 offsets and SQLite drops offsets on save.
    latest = {row.site_id: row for row in session.scalars(select(SiteLatest).where(SiteLatest.site_id.in_([c[0] for c in candidates])))}
    accepted = []
    for sid, observed, kmh, flow in candidates:
        if sid in latest and _as_utc(latest[sid].observed_utc) >= observed:
            counters["duplicate"] += 1; continue
        local = observed.astimezone(LOCAL)
        accepted.append((sid, observed, kmh, flow, local.weekday(), slot_label(local.strftime("%H:%M"))))

    # A poll spans one or two 15-minute slots: load those slots' cells in one query each, not one query per site.
    cells = {(cell.site_id, weekday, slot): cell
             for weekday, slot in {(a[4], a[5]) for a in accepted}
             for cell in session.scalars(select(SpeedCell).where(SpeedCell.weekday == weekday, SpeedCell.slot == slot))}
    for sid, observed, kmh, flow, weekday, slot in accepted:
        key = (sid, weekday, slot)
        if key not in cells:
            cells[key] = SpeedCell(site_id=sid, weekday=weekday, slot=slot, readings=0, weight=0.0,
                                   mean_spk=0.0, m2=0.0, min_spk=0.0, max_spk=0.0)
            session.add(cells[key])
        fold(cells[key], 3600.0 / kmh, flow)
        if sid in latest:
            latest[sid].observed_utc, latest[sid].speed_kmh, latest[sid].flow = observed, kmh, flow
        else:
            latest[sid] = SiteLatest(site_id=sid, observed_utc=observed, speed_kmh=kmh, flow=flow)
            session.add(latest[sid])
        counters["stored"] += 1
    session.commit()
    return counters
