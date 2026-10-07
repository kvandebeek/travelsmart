# Candidate commute catalogue

`config/commute_catalogue.yaml` names residential neighbourhoods and employment
areas. `python scripts/build_commute_catalogue.py` writes the reviewable
`config/commute_routes.csv`. The initial catalogue covers the 36 agreed areas
plus Bilzen-Hoeselt, which was mentioned as an additional example.

Each row is one commute template. In the morning its direction is `home_place`
to `work_place`; in the evening the same template reverses direction. The
`regular` tier includes up to eight nearby employment areas and four larger
regional hubs per residential area. The `watchlist` tier records specific longer
trips for occasional direct measurement. Listing a row uses no provider calls.

The **12 km cutoff applies to the actual driving route**. The generated CSV
uses approximate municipality reference coordinates only to shortlist pairs at
least 16 km apart by straight line. These are not home or work coordinates and
must not be passed to TomTom or HERE. Before activating a row, select a public
road access point near the named residential neighbourhood and another near
the employment area, ask a router for road distance, and discard any route
shorter than 12 km. A route with no valid road access stays a candidate.

`maaseik__oostende` is a watchlist route. Its `brussels_e40` marker means we
may later measure Maaseik-home → the **same** E40 corridor point and that point
→ Oostende-work, and compare their time-aware sum with occasional direct
observations. A Maaseik → Brussels office estimate plus a Brussels residence →
Oostende estimate would introduce an unmeasured cross-city transfer. The
second segment also needs a departure time shifted by the first segment's
travel duration. A route split is used only after checking that its geometry
follows the direct route closely; a city name alone is insufficient.

This catalogue is separate from the existing OSRM `all_pairs` demo in
`config/journeys.yaml`. Its `candidate_needs_access_points_and_road_distance`
status prevents it from being mistaken for an active TomTom/HERE schedule.

Area selection can later be improved using the [Statbel population grid](https://statbel.fgov.be/en/open-data/datalab-population-grid-cells-varying-size-2025)
and [VLAIO business park data](https://www.vlaio.be/nl/vlaio-netwerk/lokale-besturen/lokaal-bedrijfshuisvestingsbeleid-en-advies/data-over-bedrijven-en-bedrijventerreinen/waar-kan-je).
