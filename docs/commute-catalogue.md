# Commute catalogue

A **commute** is a town pair, such as Diepenbeek → Brussels. Each town has
several residential **start places** and one or more **employment areas**.
Every start place → employment area route between two towns feeds the same
commute record. The places give diversity; the record is the town pair.

## Building it

1. `python scripts/select_home_places.py --statbel-dir DIR` reads Statbel's
   population per statistical sector (1 January 2024) and the matching sector
   boundaries. It writes `config/commute_homes.yaml`. A town gets 2 start
   places below 60,000 inhabitants, 3 up to 150,000 and 4 above that; Brussels
   gets 5. Places are spread over the town's former municipalities
   (deelgemeenten; for Brussels, its municipalities), most populated first,
   and kept 1.5–2.5 km apart. Each is placed in the most populated sector of
   its area.
2. Employment areas are listed in `config/commute_catalogue.yaml`, either as
   reviewed coordinates or as an OpenStreetMap search.
3. `python scripts/build_commute_catalogue.py` snaps every point to the
   nearest drivable road (OSRM `nearest`). Each start place is paired with its
   nearest employment areas plus regional hubs, at most two per destination
   town. Road distances are measured with OSRM `table`, and routes under
   12 km are dropped. The script writes `config/commute_anchors.yaml` and
   `config/commute_routes.csv`. It makes no TomTom or HERE calls.

## Priority commutes

`core_routes` in `config/commute_schedule.yaml` lists town pairs that are
measured in every departure slot. The catalogue includes every start place ×
employment area combination for these pairs. The planner spends one call per
slot on the pair, rotating through the combinations by day and slot. All
other routes rotate through the month.

## Long journeys

Watchlist routes such as Maaseik → Oostende are observed directly as
calibration. Their corridor marker (for example `brussels_e40`) means they may
later be measured as two segments that meet at the **same** E40 point, with the
second segment's departure shifted by the first segment's travel time. Joining
a Maaseik → Brussels office trip to a Brussels home → Oostende trip would add a
cross-city transfer that was never measured, so that is not done.
