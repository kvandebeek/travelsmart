"""Calendar, light and Flemish traffic-event context for commute observations."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from math import acos, asin, cos, degrees, pi, radians, sin, sqrt
from zoneinfo import ZoneInfo

import httpx

from travelsmart.commute_planner import belgian_statutory_holidays, easter_sunday
from travelsmart.datex import Snapshot, parse


def _monday_on_or_before(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _vacation(day: date, start: date, days: int) -> bool:
    return start <= day < start + timedelta(days=days)


def flanders_school_day(day: date) -> bool:
    """General Flemish primary/secondary calendar; individual school closures vary."""
    if day.weekday() >= 5 or day in belgian_statutory_holidays(day.year):
        return False
    if date(day.year, 7, 1) <= day <= date(day.year, 8, 31):
        return False
    for year in (day.year - 1, day.year, day.year + 1):
        easter = easter_sunday(year)
        autumn = _monday_on_or_before(date(year, 11, 1))
        if date(year, 11, 1).weekday() == 6:
            autumn += timedelta(days=7)
        christmas = _monday_on_or_before(date(year, 12, 25))
        if date(year, 12, 25).weekday() >= 5:
            christmas += timedelta(days=7)
        carnival = easter - timedelta(days=48)
        april_first = date(year, 4, 1)
        spring = april_first + timedelta(days=(7 - april_first.weekday()) % 7)
        if easter.month == 3:
            spring = easter + timedelta(days=1)
        elif easter.day > 15 and easter.month == 4:
            spring = easter - timedelta(days=13)
        if any((_vacation(day, autumn, 7), _vacation(day, christmas, 14),
                _vacation(day, carnival, 7), _vacation(day, spring, 14))):
            return False
        if day == easter + timedelta(days=40):  # Friday after Ascension
            return False
    return True


# Official FWB decree for the 2026-27 academic year. Unknown years remain null.
FWB_2026_27 = ((date(2026, 10, 19), date(2026, 10, 30)),
               (date(2026, 12, 21), date(2027, 1, 1)),
               (date(2027, 2, 22), date(2027, 3, 5)),
               (date(2027, 4, 26), date(2027, 5, 7)))
FWB_EXTRA = {date(2026, 11, 2), date(2027, 2, 9)}


def fwb_school_day(day: date) -> bool | None:
    if not date(2026, 8, 24) <= day <= date(2027, 8, 22):
        return None
    if day.weekday() >= 5 or day in belgian_statutory_holidays(day.year) or day in FWB_EXTRA:
        return False
    if day >= date(2027, 7, 3):
        return False
    return not any(start <= day <= end for start, end in FWB_2026_27)


def solar_elevation(moment: datetime, latitude: float, longitude: float) -> float:
    """NOAA fractional-year approximation; adequate for light/dark grouping."""
    utc = moment.astimezone(timezone.utc)
    hour = utc.hour + utc.minute / 60 + utc.second / 3600
    gamma = 2 * pi / (366 if _is_leap(utc.year) else 365) * (utc.timetuple().tm_yday - 1 + (hour - 12) / 24)
    decl = (.006918 - .399912*cos(gamma) + .070257*sin(gamma) - .006758*cos(2*gamma)
            + .000907*sin(2*gamma) - .002697*cos(3*gamma) + .00148*sin(3*gamma))
    equation = 229.18 * (.000075 + .001868*cos(gamma) - .032077*sin(gamma)
                         - .014615*cos(2*gamma) - .040849*sin(2*gamma))
    solar_minutes = (hour * 60 + equation + 4 * longitude) % 1440
    hour_angle = radians(solar_minutes / 4 - 180)
    lat = radians(latitude)
    cosine_zenith = sin(lat)*sin(decl) + cos(lat)*cos(decl)*cos(hour_angle)
    return 90 - degrees(acos(max(-1, min(1, cosine_zenith))))


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def light_category(moment: datetime, point: list[float]) -> str:
    elevation = solar_elevation(moment, point[0], point[1])
    return "daylight" if elevation >= -.833 else "twilight" if elevation >= -6 else "dark"


def calendar_context(moment: datetime, origin: list[float], destination: list[float], duration_seconds: float) -> dict:
    day = moment.astimezone(ZoneInfo("Europe/Brussels")).date()
    arrival = moment + timedelta(seconds=duration_seconds)
    return {"weekday": day.weekday(), "month": day.month,
            "flanders_school_day": flanders_school_day(day), "fwb_school_day": fwb_school_day(day),
            "origin_light": light_category(moment, origin),
            "destination_light": light_category(arrival, destination)}


def fetch_datex_snapshot(url: str) -> Snapshot:
    response = httpx.get(url, timeout=30)
    response.raise_for_status()
    return parse(response.content)


def _point_distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = map(radians, a)
    lat2, lon2 = map(radians, b)
    value = sin((lat2-lat1)/2)**2 + cos(lat1)*cos(lat2)*sin((lon2-lon1)/2)**2
    return 12742.0176 * asin(sqrt(value))


def event_context(route_points: list[tuple[float, float]] | None, snapshot: Snapshot | None,
                  observed: datetime, radius_km: float = .5) -> dict | None:
    if route_points is None or snapshot is None or len(route_points) < 2:
        return None
    if abs((observed - snapshot.published).total_seconds()) > 900:
        return None
    # Sparse sampling keeps the matching cost bounded for long provider polylines.
    sampled = route_points[::max(1, len(route_points)//250)]
    if sampled[-1] != route_points[-1]:
        sampled.append(route_points[-1])
    counts = {"jam": 0, "accident": 0, "obstruction": 0, "roadworks": 0, "lane_management": 0}
    for event in snapshot.records:
        if event["kind"] not in counts or not event["geometry"]:
            continue
        if event["validity_status"] not in (None, "active", "definedByValidityTimeSpec"):
            continue
        if any(_point_distance_km(point, event_point) <= radius_km
               for point in sampled for event_point in event["geometry"]):
            counts[event["kind"]] += 1
    return {"source": "flemish_traffic_centre_datex", "published_at": snapshot.published.isoformat(),
            "matching_radius_km": radius_km, "flemish_events_near_route": counts}
