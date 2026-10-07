from __future__ import annotations
import os
from pathlib import Path
from sqlalchemy import DateTime, Float, Integer, String, create_engine
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
