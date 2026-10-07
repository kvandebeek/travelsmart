from __future__ import annotations
import argparse, logging
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from travelsmart.analytics import grouped_statistics
from travelsmart.collectors import collect_many
from travelsmart.config import ROOT, load_settings
from travelsmart.db import Measurement, make_session_factory
from travelsmart.export import export_static
from travelsmart.providers import OSRMProvider

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
settings = load_settings(); Session = make_session_factory(); app = FastAPI(title="TravelSmart")

def measurements(journey_id):
    with Session() as session:
        return session.scalars(select(Measurement).where(Measurement.journey_id == journey_id).order_by(Measurement.timestamp_utc.desc())).all()

@app.get("/api/health")
def health(): return {"status": "ok"}
@app.get("/api/locations")
def locations(): return [vars(x) for x in settings.locations.values()]
@app.get("/api/journeys")
def journeys(): return [vars(x) | {"via": list(x.via)} for x in settings.journeys.values()]
@app.get("/api/journeys/{journey_id}")
def journey(journey_id: str):
    if journey_id not in settings.journeys: raise HTTPException(404, "Unknown journey")
    item = settings.journeys[journey_id]; return vars(item) | {"via": list(item.via)}
@app.get("/api/journeys/{journey_id}/measurements")
def journey_measurements(journey_id: str):
    if journey_id not in settings.journeys: raise HTTPException(404, "Unknown journey")
    return [{"id": r.id, "timestamp_utc": r.timestamp_utc, "duration_seconds": r.duration_seconds, "distance_m": r.distance_m, "measurement_type": r.measurement_type} for r in measurements(journey_id)]
@app.get("/api/journeys/{journey_id}/statistics")
def journey_statistics(journey_id: str):
    if journey_id not in settings.journeys: raise HTTPException(404, "Unknown journey")
    return {"journey": journey_id, **grouped_statistics(measurements(journey_id))["overall"]}

if (ROOT / "public").exists(): app.mount("/", StaticFiles(directory=ROOT / "public", html=True), name="public")

def miv_collect(session) -> dict:
    """One poll of the detector feed. Detector metadata is refreshed only when the publisher changes it."""
    import yaml
    from travelsmart import miv
    from travelsmart.verified_fetch import fetch
    source = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text(encoding="utf-8"))["miv"]
    get = lambda name: fetch(source[f"{name}_url"], extra_ca=ROOT / source["extra_ca"])
    snapshot = miv.parse_data(get("data"))  # parsed once; ingest() works on the parsed snapshot
    if not miv.config_is_current(session, snapshot.config_time):
        logging.info("detector configuration changed or new: syncing %s sites", miv.sync_sites(session, get("config")))
    counters = miv.ingest(session, snapshot)
    logging.info("miv poll published=%s %s", snapshot.published.isoformat(), counters)
    return counters

def datex_collect(session) -> dict:
    """One poll of the events feed: upsert current records, log jam lengths, close records that have ended."""
    import yaml
    from travelsmart import datex
    from travelsmart.verified_fetch import fetch
    source = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text(encoding="utf-8"))["datex"]
    snapshot = datex.parse(fetch(source["url"]))
    counters = datex.ingest(session, snapshot)
    log = logging.warning if counters["status"] == "stale" else logging.info
    log("datex poll published=%s %s", snapshot.published.isoformat(), counters)
    return counters

def miv_probe(out: Path):
    """Fetch the detector feed (config + latest measurements) once and print what it contains."""
    import collections, re, yaml
    from travelsmart.verified_fetch import fetch
    source = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text(encoding="utf-8"))["miv"]
    out.mkdir(parents=True, exist_ok=True)
    for name in ("config", "data"):
        body = fetch(source[f"{name}_url"], extra_ca=ROOT / source["extra_ca"])
        (out / f"miv_{name}.xml").write_bytes(body)
        text = body.decode("utf-8", errors="replace")
        print(f"{name}: {len(body)} bytes; elements: {collections.Counter(re.findall(r'<(\w+)[ >/]', text)).most_common(12)}")
        print("  head:", " ".join(text[:600].split()))

def cli():
    parser = argparse.ArgumentParser(prog="python -m travelsmart")
    sub = parser.add_subparsers(dest="command", required=True)
    collect = sub.add_parser("collect"); collect.add_argument("--journey"); collect.add_argument("--missing", action="store_true", help="Collect only journeys with no stored measurement"); collect.add_argument("--workers", type=int, help="Parallel route requests (default: TRAVELSMART_COLLECT_WORKERS or 8)")
    sub.add_parser("export")
    sub.add_parser("miv-collect", help="Poll the Flemish detector feed once and fold fresh readings into the running statistics")
    sub.add_parser("datex-collect", help="Poll the Flemish events feed (jams, accidents, roadworks) once and store it")
    probe = sub.add_parser("miv-probe", help="Download the Flemish detector feed once and summarise it"); probe.add_argument("--out", default="data/miv-samples")
    args = parser.parse_args()
    if args.command == "collect":
        ids = [args.journey] if args.journey else [j.id for j in settings.journeys.values() if j.active]
        if args.missing:
            with Session() as session:
                existing = set(session.scalars(select(Measurement.journey_id).distinct()).all())
            ids = [jid for jid in ids if jid not in existing]
        unknown = set(ids) - settings.journeys.keys()
        if unknown: parser.error(f"Unknown journey: {unknown.pop()}")
        provider = OSRMProvider()
        logging.info("collecting %s journeys", len(ids))
        with Session() as session:
            rows, failed = collect_many(session, settings, provider, ids, workers=args.workers)
        logging.info("stored=%s failed=%s", len(rows), len(failed))
    elif args.command == "miv-collect":
        with Session() as session: miv_collect(session)
    elif args.command == "datex-collect":
        with Session() as session: datex_collect(session)
    elif args.command == "miv-probe":
        miv_probe(Path(args.out))
    else:
        with Session() as session: export_static(session, settings, ROOT / "public" / "data")
        logging.info("export completed journeys=%s", len(settings.journeys))

if __name__ == "__main__": cli()
