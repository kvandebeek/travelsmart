# TravelSmart Belgium

TravelSmart is a small, open foundation for a Belgian travel-time index. Version 0.1 monitors driving journeys between 56 Belgian towns: the five largest municipalities of each province plus a few extra Limburg towns around Diepenbeek.

It records **baseline routing estimates** from an OpenStreetMap-based OSRM router. It does not claim that values represent real historical traffic conditions.

## Architecture

Configuration (`config/`) defines locations and directional journeys. Collection writes every route estimate to SQLite. Analytics calculate P10 / median / P90 from the stored observations. `export` creates static JSON consumed by the plain HTML dashboard, independently of the API. The OSRM adapter is replaceable and a self-hosted OSRM instance is the intended production choice; the default public endpoint is for development only.

The locations are configurable in `config/locations.yaml`: Diepenbeek uses the town-hall point (Gemeenteplein 1), while Brussels uses Brussels Town Hall at Grand-Place.

## Install and run

Requires Python 3.10+.

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]" # Windows: .venv\Scripts\pip install -e ".[dev]"
python -m travelsmart collect
python -m travelsmart export
```

Open `public/index.html` using a static file server (for example `python -m http.server --directory public 8080`) and visit `http://localhost:8080`. Browsers restrict `fetch` from a directly opened `file://` page, so serving it locally is recommended.

To run the local API and dashboard together:

```bash
uvicorn travelsmart.main:app --reload
```

Open `http://127.0.0.1:8000/docs` for the API. Available endpoints include `/api/health`, `/api/locations`, `/api/journeys`, and journey-specific measurements and statistics.

## Configuration and scheduler

Location coordinates live in `config/locations.yaml`; directional journeys and weekday collection windows live in `config/journeys.yaml`. The default windows are weekdays 05:30–09:30 and 15:00–19:00, every 15 minutes. Run the scheduler on the Raspberry Pi with:

```bash
python scripts/run_scheduler.py
```

It operates on every active configured journey, never hard-coded IDs. Environment settings are documented in `.env.example`.

`all_pairs: all` in `config/journeys.yaml` creates one journey for every ordered pair of towns (56 × 55 = 3,080). Collection fetches routes in parallel (`--workers` / `TRAVELSMART_COLLECT_WORKERS`, default 8) and writes each batch to SQLite from a single thread, with one shared timestamp per tick. A full tick is thousands of routing requests, so use a **self-hosted OSRM** for that; the public demo server will throttle you. To start with fewer, list town ids under `all_pairs` instead of `all`. New towns show `n/a` until collected: `python -m travelsmart collect --missing`.

## Static export and tests

```bash
python -m travelsmart export
pytest
```

Export writes `public/data/index.json`, locations, journeys, and one JSON detail document per journey. Copy `public/` to GitHub Pages after export.

The dashboard is three plain files (`index.html`, `style.css`, `app.js`) with no build step: a route picker, a heat-coloured matrix of typical times, and a detail card with a Fast / Typical / Slow range bar plus departure-time and weekday breakdowns. Routes are deep-linkable (`#diepenbeek_brussels`) and it follows the system light/dark setting. Routes with fewer than `reliable_samples` (default 20, in `config/journeys.yaml`) samples are labelled "Preview".

## Known limitations

v0.1 is driving-only. Town lists are per-province top-5 by municipal population from memory (not verified against Statbel) and can be edited in `config/locations.yaml`. OSRM routes do not contain live traffic. The testing threshold is currently one sample (`sufficient_samples` in `config/journeys.yaml`); raise it before treating P10/P90 as meaningful. Public OSRM servers must not be relied upon for production; self-host OSRM or introduce another permitted provider behind the provider interface.
