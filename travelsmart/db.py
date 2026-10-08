from __future__ import annotations
import os
from pathlib import Path
from sqlalchemy import Boolean, DateTime, Float, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

class Base(DeclarativeBase): pass

class Measurement(Base):
    __tablename__ = "measurements"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    journey_id: Mapped[str] = mapped_column(String(100), index=True)
    timestamp_utc: Mapped[object] = mapped_column(DateTime(timezone=True), index=True)
    timestamp_local: Mapped[object] = mapped_column(DateTime(timezone=True))
    weekday: Mapped[int] = mapped_column(Integer)
    departure_time: Mapped[str] = mapped_column(String(5))
    provider: Mapped[str] = mapped_column(String(50))
    distance_m: Mapped[float] = mapped_column(Float)
    duration_seconds: Mapped[float] = mapped_column(Float)
    route_geometry: Mapped[str | None] = mapped_column(String, nullable=True)
    measurement_type: Mapped[str] = mapped_column(String(30), default="baseline")
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True))

class MivSite(Base):
    """One detector site (all lanes of one carriageway) of the Flemish loop-detector network."""
    __tablename__ = "miv_sites"
    site_id: Mapped[str] = mapped_column(String(40), primary_key=True)   # "<road segment>@<km marker>", e.g. "A0010001@10,371"
    name: Mapped[str] = mapped_column(String(200))
    road: Mapped[str] = mapped_column(String(20))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    lanes: Mapped[int] = mapped_column(Integer)

class MivLane(Base):
    """Maps a detector lane (unieke_id in the data feed) to the site it belongs to."""
    __tablename__ = "miv_lanes"
    unieke_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    site_id: Mapped[str] = mapped_column(String(40), index=True)

class SpeedCell(Base):
    """Running statistics of how slow traffic is at a site: seconds per km, per weekday and 15-minute slot.

    Only aggregates are kept (weighted Welford: weight = vehicles counted), never the individual readings.
    """
    __tablename__ = "speed_cells"
    site_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    weekday: Mapped[int] = mapped_column(Integer, primary_key=True)     # Mon=0, Belgian local time
    slot: Mapped[str] = mapped_column(String(5), primary_key=True)      # "HH:MM", start of the 15-minute slot
    readings: Mapped[int] = mapped_column(Integer, default=0)
    weight: Mapped[float] = mapped_column(Float, default=0.0)           # vehicles counted
    mean_spk: Mapped[float] = mapped_column(Float, default=0.0)         # weighted mean seconds per km
    m2: Mapped[float] = mapped_column(Float, default=0.0)               # weighted sum of squared deviations
    min_spk: Mapped[float] = mapped_column(Float, default=0.0)
    max_spk: Mapped[float] = mapped_column(Float, default=0.0)

class SiteLatest(Base):
    """Newest accepted reading per site; feeds the "currently" status and de-duplicates polls."""
    __tablename__ = "site_latest"
    site_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    observed_utc: Mapped[object] = mapped_column(DateTime(timezone=True))
    speed_kmh: Mapped[float] = mapped_column(Float)
    flow: Mapped[int] = mapped_column(Integer)

class TrafficEvent(Base):
    """One DATEX II situation record (jam, accident, obstruction, roadworks, lane closure) and its lifetime.

    The feed lists only current records and gives a jam no end time: a record that drops out of the feed has
    ended. It was last seen at `last_seen_utc` and found gone at `gone_utc` (one poll later); for jams, which are
    re-versioned every minute while they last, `version_time_utc` is the best estimate of when it ended.
    """
    __tablename__ = "traffic_events"
    record_id: Mapped[str] = mapped_column(String(60), primary_key=True)
    situation_id: Mapped[str] = mapped_column(String(60), index=True)
    kind: Mapped[str] = mapped_column(String(30), index=True)          # jam, accident, obstruction, roadworks, lane_management
    record_type: Mapped[str] = mapped_column(String(60))               # DATEX II class, e.g. AbnormalTraffic
    subtype: Mapped[str | None] = mapped_column(String(120), nullable=True)   # e.g. queuingTraffic, roadClosed
    validity_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    start_utc: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    planned_end_utc: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_seen_utc: Mapped[object] = mapped_column(DateTime(timezone=True))
    last_seen_utc: Mapped[object] = mapped_column(DateTime(timezone=True))
    gone_utc: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    version: Mapped[int] = mapped_column(Integer)
    version_time_utc: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    direction: Mapped[str | None] = mapped_column(String(20), nullable=True)        # ALERT-C: positive / negative
    primary_location: Mapped[int | None] = mapped_column(Integer, nullable=True)    # ALERT-C location code, Belgian table 601
    secondary_location: Mapped[int | None] = mapped_column(Integer, nullable=True)
    queue_length_m: Mapped[int | None] = mapped_column(Integer, nullable=True)      # jams: latest length
    max_queue_length_m: Mapped[int | None] = mapped_column(Integer, nullable=True)  # jams: longest length seen
    geometry: Mapped[str | None] = mapped_column(String, nullable=True)  # JSON [[lat, lon], ...] WGS84; jams: at their longest
    min_lat: Mapped[float | None] = mapped_column(Float, nullable=True)  # bounding box, for matching events to routes
    min_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_lon: Mapped[float | None] = mapped_column(Float, nullable=True)

class JamObservation(Base):
    """A jam as seen in one poll: how long it was and where its two ends were (the feed does not say which is the head)."""
    __tablename__ = "jam_observations"
    record_id: Mapped[str] = mapped_column(String(60), primary_key=True)
    observed_utc: Mapped[object] = mapped_column(DateTime(timezone=True), primary_key=True, index=True)  # feed publication time
    version: Mapped[int] = mapped_column(Integer)
    queue_length_m: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end1_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    end1_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    end2_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    end2_lon: Mapped[float | None] = mapped_column(Float, nullable=True)

class LegMeasurement(Base):
    """One provider's time for one network edge at one moment (docs/network-design.md §7).

    The raw firehose: ~100,000 rows a day, kept on the collector machine and never in Git. Profiles
    in §8 are built from these. A leg is stored for the moment it is actually driven, which on a
    chain is its own departure time, not the chain's.

    Rejected measurements are stored too, with `accepted` false and the reason: a provider that keeps
    routing around an edge is evidence about the network, not noise to drop silently.
    """
    __tablename__ = "leg_measurements"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    edge_id: Mapped[str] = mapped_column(String(20), index=True)
    provider: Mapped[str] = mapped_column(String(20), index=True)
    chain_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    departure_utc: Mapped[object] = mapped_column(DateTime(timezone=True), index=True)
    weekday: Mapped[int] = mapped_column(Integer)          # local weekday, 0 = Monday
    half_hour: Mapped[int] = mapped_column(Integer)        # local half hour of the day, 0-47
    seconds: Mapped[float] = mapped_column(Float)
    free_flow_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    typical_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    metres: Mapped[float | None] = mapped_column(Float, nullable=True)
    roads: Mapped[str | None] = mapped_column(String(100), nullable=True)   # what the provider drove
    accepted: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    rejected_because: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True))


class SourceState(Base):
    __tablename__ = "source_state"
    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(String(200))

def database_url() -> str:
    return os.getenv("TRAVELSMART_DATABASE_URL", "sqlite:///data/travelsmart.db")

def make_session_factory(url: str | None = None):
    url = url or database_url()
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///" )).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)
