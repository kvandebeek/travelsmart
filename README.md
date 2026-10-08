<div align="center">

# 🚗 TravelSmart Belgium

### *When is the best moment to leave?*
**A national road network measured around the clock, so any trip in Belgium gets a "when to leave" heatmap.**

[![Publish dashboard](https://github.com/kvandebeek/travelsmart/actions/workflows/publish.yml/badge.svg)](https://github.com/kvandebeek/travelsmart/actions/workflows/publish.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)
![No build step](https://img.shields.io/badge/dashboard-zero%20build%20step-ff69b4)

### [📐 Read the network design →](docs/network-design.md)

</div>

---

## 💡 The idea

Every commuter knows the feeling: leave ten minutes later and you lose half an hour on the ring road. Maps can tell you how long a trip takes **right now**, but they can't tell you **when you should have left**.

TravelSmart builds that answer from repeated measurements. Belgium's motorways and main regional roads become a network of short stretches, each measured again and again by Google Maps, TomTom and HERE. Every stretch gets a profile for every half hour of every weekday. Stitched together, they give any trip A → B an answer like this (an illustration, not a measurement):

> 🕖 *"Diepenbeek → Brussels: Tuesday to Thursday, leaving at 06:30 or after 09:30 is fastest; 07:30 is the worst half hour of the week."*

## 🚧 Status

The project is being rebuilt around that network. **[docs/network-design.md](docs/network-design.md)** describes the full design: node and edge types, endpoints (business parks, schools, hospitals, government buildings, stations, neighbourhoods), how each provider measures, storage, profiles, routing and the pages. The first commute prototype (fixed town pairs and corridors) and its data were removed on 8 October 2026.

What works today:

- **Google Maps browser collector**: `scripts/collect_google_maps.py` reads every route option Google lists (time, distance, road and traffic note) in a headless browser. It is the engine the new edge collector will build on.
- **Capture import**: `scripts/import_google_maps_captures.py` merges local captures into one observation file with each route's road numbers and alternatives.
- **Open-data feeds and the OSRM baseline** (below).

## 🚀 Run it yourself

Requires Python 3.10 or newer.

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev,google-maps-capture]"   # Windows: .venv\Scripts\pip install ...
.venv/bin/python -m playwright install chromium
pytest
```

Collect Google Maps travel times for a few routes (headless; add `--no-headless` to watch the browser):

```bash
python scripts/collect_google_maps.py --origin "Hasselt, Belgium" --destination "Brussels, Belgium" --once
python scripts/import_google_maps_captures.py
```

The collector opens a fresh browser tab for every route: with some browser profiles Chromium otherwise keeps one page process per visit alive until the machine runs out of memory. When Google shows its consent screen, it clicks **Reject all**; if that fails, the capture is stored as `consent_required`.

<details>
<summary><b>🗺️ Also included: an OSRM baseline and Flemish live feeds</b></summary>

A **baseline travel-time index** for 56 Belgian towns using an OpenStreetMap OSRM router, plus collectors for Flemish **loop detectors (MIV)** and **DATEX II road events**, which run locally or on a Raspberry Pi. In the new design they add context, for example road events that explain a jam on a stretch.

```bash
python -m travelsmart collect && python -m travelsmart export   # OSRM baseline
docker compose up -d                                            # MIV + DATEX pollers
uvicorn travelsmart.main:app --reload                           # API at /docs
```

OSRM has no live traffic. It is close to reality on rural roads but optimistic in city centres, so treat it as a baseline, not a commute time.

</details>

## 🙏 Data and attribution

| Source | Used for | Terms |
| --- | --- | --- |
| [Google Maps](https://www.google.com/maps) (browser) | Travel times and route alternatives per stretch | [Google Maps terms](https://www.google.com/help/terms_maps/) |
| [TomTom Routing API](https://developer.tomtom.com/routing-api/documentation) | Travel times per stretch (planned) | Free tier |
| [HERE Routing v8](https://www.here.com/docs/bundle/routing-api-developer-guide-v8/page/README.html) | Calibration sample (planned) | Free tier |
| [Statbel](https://statbel.fgov.be/en/open-data) | Municipalities, population per statistical sector | Open data licence |
| [Vlaams Verkeerscentrum DATEX II](https://www.verkeerscentrum.be/) | Road events | CC BY. © Agentschap Wegen en Verkeer – Vlaams Verkeerscentrum |
| [MIV open data](https://miv-opendata.belfla.be/) | Loop-detector speeds | Open data |
| [OpenStreetMap](https://www.openstreetmap.org/copyright) via Overpass, OSRM and Nominatim | Road network, junctions, baseline | ODbL |

<div align="center">
<br/>
<sub>🇧🇪 Built for Belgian commuters who would rather spend 20 extra minutes at home than in a traffic jam on the E40.</sub>
</div>
