# TravelSmart network design

Status: phase 1 builder, automated topology audit and local review map built, 8 October 2026. The
old commute routes, corridors and their data were removed on the same day to start fresh. The audit's
systemic findings were fixed on 9 October 2026: unnumbered carriageways paired with a numbered road
were being dropped, and backbone roads outside the N/R numbering (Brussels B-roads, A-roads and
E-route carriageways at interchanges) were not recognised at all. What the audit still reports is
either a genuine OpenStreetMap source gap to edit upstream, or a deliberate limit of the compact
graph (exit access stops after two hops of ordinary local street). Phase 2 has started: stations are
loaded as endpoint clusters with access edges.

## 1. Goal

For any trip A → B in Belgium, show **when to leave**: a heatmap of the fastest total travel time
for every half hour of every weekday, the best departure windows, and the route with each stretch
coloured by how it compares with its usual time. Later: round trips (leave A at *t1*, stay *X*
hours, leave B at *t2*).

The answer is not a live travel time. It is a typical-week profile, built from weeks of repeated
measurements and stated with its uncertainty ("these slots are equally good").

## 2. The model in one paragraph

Belgium's main roads become a **directed graph**. Nodes sit on the road; **edges** are short
stretches between neighbouring nodes. Every edge gets a travel-time profile: 7 weekdays × 48 half
hours. Trip start and end points (**endpoints**: business parks, schools, hospitals, government
buildings, stations, residential neighbourhoods) hang off the graph through short **access edges**.
A trip leaving at time *t* is found with a time-dependent fastest-path search: the first edge is
driven at *t*, the next at *t* plus the first edge's time, and so on.

## 3. Nodes

Two roles: **network nodes** that routes pass through, and **endpoints** where trips start or end.

### 3.1 Network nodes

| Type | Where | Directional? | Purpose |
|---|---|---|---|
| **Carriageway node** (motorways, motorway rings) | On the carriageway, just after each on-ramp merge | Yes: one per direction | Through traffic. Chaining two motorway edges passes the node without leaving the motorway |
| **Connection node** (motorway exits) | Where the slip roads meet the local road | No | Joining and leaving the motorway; links to regional roads and endpoints |
| **Regional-road node** (N-roads) | Crossings of two backbone N-roads; N-road ↔ connection node; town gateways (where the N-road enters/leaves a built-up area); extra nodes so no edge exceeds the length cap | No on single carriageway; yes on dual-carriageway stretches | Regional traffic, town passages |

Rules:

- A motorway-to-motorway interchange is **not** a node. A **transfer edge** runs from the last
  carriageway node before it on motorway A to the first after it on motorway B.
- Rings are ordinary roads that close a loop: R0, the motorway parts of R1 and R4 use carriageway and
  connection nodes; city rings (R40 Ghent, R23 Leuven, R70/R71 Hasselt, R30 Bruges, R8 Kortrijk, …)
  use regional-road nodes.
- Nodes are generated from OpenStreetMap (motorway junctions, on-ramp merge points, road crossings),
  then checked for disconnected components, isolated nodes and nearby unjoined road ends. The
  remaining flagged cases are reviewed on a map before use.

### 3.2 Which roads form the backbone

- **All motorways and motorway rings** (A/E/R roads). OpenStreetMap has 1,479 motorway-junction
  points in Belgium (exits and interchange branches, both directions).
- **All main N-roads: N1–N99** and their lettered variants (N1a, N4d, …): 210 road numbers.
- **Three-digit N-roads** (746 numbers) only where needed to reach an endpoint cluster or close an
  obvious gap. They are the local network; including all of them would multiply the edge count
  without changing most fastest routes.

### 3.3 Endpoints

| Category | Source (open data) | Weight | Typical rhythm |
|---|---|---|---|
| Business parks | Flanders: VLAIO/Geopunt business-park register; Wallonia: SPW business-activity zones; Brussels: perspective.brussels | Area / jobs where available | 07:00–09:00, 16:00–18:30 |
| Schools | Flemish education department school locations; Fédération Wallonie-Bruxelles school register | Pupils where available | 08:15 and 15:30 (Wed ~12:00), school days only |
| Hospitals | Federal hospital list (campuses) | Beds | Shift changes ~06:30, ~14:30, ~22:30 |
| Government | Federal, regional and EU office clusters (curated list + OpenStreetMap office=government) | Staff where known | Office hours |
| Stations and park-and-ride | NMBS/SNCB GTFS stops; OpenStreetMap park_ride | Passengers where available | Peaks around train times |
| Residential neighbourhoods | Statbel population per statistical sector | Population | Origins of the morning peak |
| Other large attractors | Airports, ports (Antwerp, Zeebrugge, Ghent), large shopping centres | Curated | Mixed |

Schools and residential neighbourhoods number in the thousands. They are **clustered**: endpoints
that share the same nearest network node(s) form one cluster with one set of access edges. A trip
"to school X" uses its cluster's access edges.

## 4. Edges

| Kind | Between | Target length | Measured |
|---|---|---|---|
| Backbone edge | Neighbouring network nodes along one road | 2–6 km; hard cap ~8 km | Often (see §6) |
| Transfer edge | Across a motorway interchange | Interchange ramp + 1–3 km | Often |
| Ramp edge | Connection node ↔ carriageway node | < 1.5 km | Rarely; mostly stable |
| Access edge | Endpoint cluster ↔ its 1–2 nearest network nodes | 0.5–5 km | A few times per day |

Each edge stores: id (stable across network versions), from/to node, kind, **intended road numbers**
(e.g. `["E40"]`, `["N2"]`), expected length and free-flow time (from the OpenStreetMap path), and
its region.

**Edge validation.** A measurement only counts for an edge if the provider drove the intended road:

- Google: the "via" text must contain the intended road number, and distance within ±15% of expected.
- TomTom/HERE: leg length within ±15% of expected (their legs carry no road names).

Edges that keep failing validation have badly placed nodes and are fixed in the network, not in
the data.

## 5. Size (phase 1 graph from the 7 October 2026 OSM extract)

| Part | Current graph / estimate for later phases |
|---|---|
| Carriageway + connection nodes | 1,152 + 2,616 |
| Regional-road + length split nodes | 1,453 + 1,493 |
| Backbone + transfer edges (directed) | 1,220 motorway + 8,879 regional + 341 transfer |
| Ramp and shared-junction edges (directed) | 1,354 on + 1,541 off + 538 ramp links + 66 zero-distance junction turns |
| Endpoint clusters (phase 2) | 503 for stations; ~1,500–3,000 once every category is loaded |
| Access edges (phase 2, directed) | 1,569 for stations (1,437 along roads, 132 estimated by distance) |

The builder includes the shortest legal local road paths found within 15 km in both directions
between motorway exits and the N1–N99/R1–R99 backbone. Exact OSM junctions remain separate so
short roads between them retain their length and one-way restriction. The automated audit reports
no missing links at numbered OSM junctions and no unrepresented OSM junctions. Of 2,172 public-road exit
connections checked, 31 have no selected local road path to the numbered backbone; 11 cannot
reach it in the exit direction and 6 cannot be reached from it in the entrance direction. The
remaining 80 proximity candidates need a separate topology check: bridges and parallel
carriageways can be close without joining.

This is too much to measure everything every half hour, so measurement is **tiered** (§6).

## 6. Measuring

Three providers, one network. Each measures **edges**, never whole trips.

### 6.1 TomTom: the workhorse for the backbone

One Calculate Route request takes up to **150 waypoints** and returns a summary per leg:
`travelTimeInSeconds`, `trafficDelayInSeconds`, `departureTime`/`arrivalTime`, and with
`computeTravelTimeFor=all` also `noTrafficTravelTimeInSeconds` and
`historicTrafficTravelTimeInSeconds` (TomTom's own typical time). The backbone is cut into
**chains**: paths along neighbouring nodes, up to ~150 legs each. One request then measures a whole
chain, in exact seconds. Each leg's moment is its own `departureTime`, so legs are stored for the
moment they are actually driven. TomTom documents per-leg times but not explicitly that traffic on
later legs is evaluated for the time they are reached; the first test call checks this by
comparing a chain's legs with single-leg requests.

Budget: the free tier is 20,000 requests/month per account. Two accounts, minus a reserve, give
roughly 1,200 requests/day. If a chain request counts as one transaction (undocumented; checked on
the usage dashboard after the first calls), ~100 legs per chain means ~120,000 edge measurements
a day: every backbone edge roughly every 1–2 hours, around the clock.

### 6.2 Google Maps: second opinion, alternatives and exact roads

The browser collector measures one edge per search. It stores the recommended route and Google's
alternatives (already built), and, pending a working parse, exact seconds from Google's directions
response instead of whole minutes. One browser does ~500 edges/hour; a Raspberry Pi with 2–3
browsers ~1,000–1,500/hour.

Use: (1) validation that an edge follows its intended road, (2) a second, independent travel time,
(3) alternatives show the parallel N-road and its time at the same moment. Priority goes to edges
where TomTom and Google disagree most, and to edges with high variance.

### 6.3 HERE: calibration sample

HERE's cap (96/day, 3,000/month) is small. Routing v8 accepts many `via` waypoints (HERE
recommends at most ~100); each stopover via starts a section with its own `duration` (with
traffic), `baseDuration` (free flow) and, on request, `typicalDuration`. Use it for a rotating
sample of chains, as a third opinion to detect provider bias.

### 6.4 Access and ramp edges

Measured a few times per day each (TomTom chains through endpoint clusters, or Google), because
local stretches change less over the day. Their profile falls back on weekday → all days → usual.

### 6.5 Schedule

Collection runs 24/7 on an always-on machine (Raspberry Pi, or the PC meanwhile). Rates are paced
evenly over the day; the budget is checked per provider per day and per rolling 30 days.

## 7. Storage

Raw measurements are too many for Git: ~100,000+ leg rows a day.

- **Raw**: per-leg rows (edge, provider, moment, seconds, free-flow seconds where available, road,
  alternatives) in **SQLite on the collector machine**, with a daily backup.
- **Published**: a compact **profile** per edge and provider: 7 × 48 cells, each with count,
  median and spread, over a rolling window (e.g. the last 8 weeks). The file is replaced, not
  appended, so it stays a few MB.
- Git holds code, network definition and published profiles, never the raw firehose.

## 8. From measurements to profiles

Per edge and provider:

1. **Cells**: median travel time per weekday × half hour, plus count and spread.
2. **Usual time**: the median of the half-hour medians over all days. Each half hour counts once,
   so heavily measured hours don't dominate.
3. **Compared with usual**: cell median / usual − 1.
4. **Combining providers**: TomTom is the reference level per edge. Google and HERE are scaled by
   their per-edge median ratio to TomTom, then merged. Disagreement between providers is kept as
   an uncertainty measure.
5. **Fallbacks** when a cell is thin: that weekday's cell → all days' cell for that half hour →
   usual time.
6. **Context** (phase 6): school days vs holidays, weather, daylight, road events (DATEX) and
   detector speeds (MIV). It's used to split or explain profiles, not to replace measurements.

## 9. Routing

Time-dependent fastest path over backbone, transfer, ramp and access edges. An edge's time at
moment *t* is interpolated between its two nearest half-hour cells. The search runs **in the
browser** on the published profiles: no server, and any A → B works.

Per A → B and weekday, the planner computes the fastest trip for all 48 departure half hours, and
which edges the route uses.

**Validation.** A small rotating sample of direct A → B trips is measured as a whole (Google and
TomTom). Comparing them with the stitched route shows how well chaining works, per region and time
of day, and is published alongside the results.

## 10. What the user sees

1. **Plan a trip**: pick A and B (an endpoint, or any point snapped to the network). A heatmap
   shows weekdays × half hours of the fastest total time; the best windows are marked and cells
   within measurement noise of the best are shown as equally good. Clicking a cell shows that
   route on the map, each stretch coloured by how it compares with its usual time.
2. **Network view**: the whole network coloured by "compared with usual" for a chosen weekday and
   time, with a time slider and play button. It's the national version of the old calm map.
3. **Round trip** (later): best pair of departure times for A → B → A with a stay of X hours.

## 11. Phases

| Phase | Delivers |
|---|---|
| 0 ✅ | Old routes, corridors and data removed; old cloud collection stopped |
| 1 (in review) | Network builder: nodes and edges from OpenStreetMap, intended roads, expected lengths; automated topology audit and review map |
| 2 | Endpoints: open datasets per category, clustering, access edges |
| 3 | Collectors: TomTom chains, HERE sample, Google edges with exact seconds and road validation; raw SQLite store; 24/7 scheduler for the Pi |
| 4 | Profiles: cells, usual, provider calibration, publishing; stitched-vs-direct validation |
| 5 | Pages: trip planner with heatmap (in-browser routing), network view |
| 6 | Context and significance: school calendar, weather, events; "equally good" bands |

Each phase is usable on its own: phase 1 gives a reviewable network map, phases 3–4 start the
weeks of collection the later phases need. Collection should start as early as possible.

## 12. Open points

- **Google exact seconds**: Google's directions response appears to contain durations in seconds
  next to the rounded labels; this needs a reliable parse. Without it, short Google edges are
  limited by whole-minute rounding.
- **Provider terms: a real constraint, deferred by the owner on 8 October 2026** (design proceeds as described; revisit before relying on stored TomTom/HERE data publicly).
  - TomTom's developer terms (13.3) prohibit "the caching or storing of any Results", except
    short-term caching within the response's cache headers, and the free plan only permits
    "Evaluation Use" (internal evaluation and testing). Building weeks of stored profiles from
    free-tier TomTom data conflicts with that; the earlier commute prototype did the same.
  - HERE's terms need the same check.
  - Google Maps terms restrict scraping and bulk storage.
  - Options: a paid TomTom/HERE plan whose terms allow storing derived data; or Google plus
    open data as the stored sources, with TomTom/HERE only for live checks that aren't stored.
    Keeping raw data off the public repo (§7) reduces exposure either way.
- **Transactions per chain request** are undocumented for both TomTom and HERE; check the usage
  dashboards after the first chain calls before relying on the budget in §6.1.
