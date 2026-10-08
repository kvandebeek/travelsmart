"""Road data for the network from OpenStreetMap (data © OpenStreetMap contributors, ODbL).

Read from Geofabrik's daily Belgium extract (a .osm.pbf file), so building the network needs no
query server. Download it to data/osm/ with `python scripts/build_network.py --download`.
Two passes keep memory low: first the selected roads, then only the coordinates they use.
"""

from __future__ import annotations

import re
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from travelsmart.config import ROOT

EXTRACT_URL = "https://download.geofabrik.de/europe/belgium-latest.osm.pbf"
EXTRACT = ROOT / "data" / "osm" / "belgium-latest.osm.pbf"
# Backbone roads: motorways with their slip roads, and the main regional roads N1-N99 (with lettered
# variants such as N1a). Roundabouts without a road number connect N-road pieces, so they come too.
MOTORWAY = {"motorway", "motorway_link"}
REGIONAL = {"trunk", "trunk_link", "primary", "primary_link", "secondary", "secondary_link",
            "tertiary", "tertiary_link", "unclassified", "residential"}
ROUNDABOUT_ROADS = {"trunk", "primary", "secondary", "tertiary"}
MAIN_N_ROAD = re.compile(r"(^|;)\s*N\s?[0-9]{1,2}[a-z]?\s*(;|$)")
LOCAL_N_ROAD = re.compile(r"(^|;)\s*N\s?[0-9]{3}[a-z]?\s*(;|$)")
RING_ROAD = re.compile(r"(^|;)\s*R\s?[0-9]{1,2}[a-z]?\s*(;|$)")
# Belgium also numbers backbone-grade roads outside the N/R scheme: "A" (regional roads and
# motorway-grade extensions, e.g. A112 in Antwerp), "B" (Brussels-Capital Region, e.g. B201) and
# "E" (European route overlays, e.g. E314). A non-motorway carriageway at a complex interchange or
# in Brussels often carries only one of these refs, sometimes as nat_ref rather than ref (the AWV
# convention for a motorway-grade road's own number alongside its E-route overlay).
AUX_BACKBONE_ROAD = re.compile(r"(^|;)\s*[ABE]\s?[0-9]{1,4}[a-z]?\s*(;|$)")
SELECTION_VERSION = 14
EXIT_ACCESS_HOPS = 2
PROGRESS_EVERY_WAYS = 10_000
PROGRESS_EVERY_NODES = 50_000


@dataclass
class OsmData:
    """Ways (id -> {"nodes": [...], "tags": {...}}), node coordinates and motorway-junction tags."""
    ways: dict[int, dict] = field(default_factory=dict)
    points: dict[int, tuple[float, float]] = field(default_factory=dict)
    junctions: dict[int, dict] = field(default_factory=dict)
    access_classes: dict[int, set[str]] = field(default_factory=dict)
    # Unnumbered carriageways kept only because they pair with a numbered backbone road (see
    # _add_paired_backbone_carriageways). Marked separately so select_network_ways keeps them as
    # backbone rather than subjecting them to its exit-connector search, which they would not survive.
    paired_carriageways: set[int] = field(default_factory=set)


def wanted(tags: dict) -> bool:
    highway = tags.get("highway", "")
    if highway in MOTORWAY:
        return True
    if highway in REGIONAL and (MAIN_N_ROAD.search(tags.get("ref", "")) or RING_ROAD.search(tags.get("ref", ""))
                                 or AUX_BACKBONE_ROAD.search(tags.get("ref", ""))
                                 or AUX_BACKBONE_ROAD.search(tags.get("nat_ref", ""))):
        return True
    return tags.get("junction") == "roundabout" and highway in ROUNDABOUT_ROADS


def connector_candidate(tags: dict) -> bool:
    """Roads that may fill a short gap from an exit to the numbered backbone."""
    highway = tags.get("highway", "")
    if any(tags.get(key) in {"no", "private"} for key in ("access", "vehicle", "motor_vehicle")):
        return False
    return (highway in REGIONAL and LOCAL_N_ROAD.search(tags.get("ref", "")) is not None or
            highway in {"trunk_link", "primary_link", "secondary_link", "tertiary_link",
                        "primary", "secondary", "tertiary"})


def exit_access_candidate(tags: dict) -> bool:
    """Small public roads that meet a slip road directly.

    Keeping this narrow preserves the compact graph while allowing exits whose first public road is
    unnumbered. Service roads remain outside the routing network.
    """
    if any(tags.get(key) in {"no", "private"} for key in ("access", "vehicle", "motor_vehicle")):
        return False
    return tags.get("highway") in {"unclassified", "residential"}


def paired_carriageway_candidate(tags: dict) -> bool:
    """An unnumbered, one-way regional road that might pair with a named numbered backbone road.

    Checked independently of wanted()/connector_candidate(): a bare primary/secondary/tertiary way
    (with no ref) already satisfies connector_candidate(), so a pairing candidate is often selected
    into data.ways by that rule too. Selection there does not make it backbone on its own (see
    connectors.py select_network_ways), so this check must still flag it for the pairing match.
    """
    return (tags.get("highway") in REGIONAL and bool(tags.get("name")) and not tags.get("ref") and
            tags.get("oneway") in {"yes", "true", "1", "-1", "reverse"})


def _add_paired_backbone_carriageways(data: OsmData, candidates: dict[int, dict]) -> int:
    """Keep the unnumbered, one-way carriageway(s) paired with a named numbered road.

    OSM commonly tags the road number on only one side of a divided regional road, and splits that
    unnumbered side into several way segments, often interrupted by an unnamed roundabout arc where
    the carriageways briefly merge. A segment is included when it shares an endpoint with either a
    selected numbered road or an already-included segment, of the same name and highway class; the
    match follows the whole chain back from the numbered road's endpoint, bridging transparently
    across intervening roads already kept as backbone (e.g. that roundabout arc, regardless of its
    own name) so the chain is not broken by them. This never broadens the selection to an ordinary
    unnamed local road, since only an already-backbone way can serve as such a bridge.
    """
    ends_of = defaultdict(list)
    for way_id, way in candidates.items():
        tags, nodes = way["tags"], way["nodes"]
        key = (tags["highway"], tags["name"])
        ends_of[(nodes[0], *key)].append(way_id)
        ends_of[(nodes[-1], *key)].append(way_id)

    bridges_of = defaultdict(list)  # (node, highway) -> backbone way ids touching it there
    for way_id, way in data.ways.items():
        if wanted(way["tags"]):
            highway = way["tags"].get("highway")
            bridges_of[(way["nodes"][0], highway)].append(way_id)
            bridges_of[(way["nodes"][-1], highway)].append(way_id)

    frontier = {
        (node, way["tags"].get("highway"), way["tags"].get("name"))
        for way in data.ways.values()
        if wanted(way["tags"]) and way["tags"].get("name")
        for node in (way["nodes"][0], way["nodes"][-1])
    }
    added: dict[int, dict] = {}
    crossed: set[tuple[int, str]] = set()
    while frontier:
        node, highway, name = frontier.pop()
        for way_id in ends_of.get((node, highway, name), ()):
            if way_id in added:
                continue
            way = candidates[way_id]
            added[way_id] = way
            frontier.add((way["nodes"][0], highway, name))
            frontier.add((way["nodes"][-1], highway, name))
        for bridge_id in bridges_of.get((node, highway), ()):
            if (bridge_id, name) in crossed:
                continue
            crossed.add((bridge_id, name))
            bridge = data.ways[bridge_id]
            for other in ({bridge["nodes"][0], bridge["nodes"][-1]} - {node}):
                frontier.add((other, highway, name))
    for way_id, way in added.items():
        data.ways[way_id] = way
        data.paired_carriageways.add(way_id)
    return len(added)


def load_belgium(extract: Path = EXTRACT, progress=print) -> OsmData:
    import osmium   # optional dependency: pip install osmium

    if not extract.exists():
        raise FileNotFoundError(f"{extract} is missing; run scripts/build_network.py --download first")
    cache = extract.with_suffix(".roads.json")
    stamp = [extract.stat().st_size, extract.stat().st_mtime_ns]
    if cache.exists():
        saved = json.loads(cache.read_text(encoding="utf-8"))
        if saved.get("extract_stamp") == stamp and saved.get("selection_version") == SELECTION_VERSION:
            progress(f"Using cached road selection from {cache}")
            return OsmData(
                ways={int(k): v for k, v in saved["ways"].items()},
                points={int(k): tuple(v) for k, v in saved["points"].items()},
                junctions={int(k): v for k, v in saved["junctions"].items()},
                access_classes={int(k): set(v) for k, v in saved.get("access_classes", {}).items()},
                paired_carriageways=set(saved.get("paired_carriageways", ())),
            )
    data = OsmData()
    progress("OSM scan 0%: selecting backbone and candidate connector roads")
    selected = 0
    paired_carriageways = {}
    for scanned, way in enumerate(osmium.FileProcessor(str(extract), osmium.osm.WAY), 1):
        tags = dict(way.tags)
        nodes = [node.ref for node in way.nodes]
        if wanted(tags) or connector_candidate(tags):
            data.ways[way.id] = {"nodes": nodes, "tags": tags}
            selected += 1
        if paired_carriageway_candidate(tags):
            paired_carriageways[way.id] = {"nodes": nodes, "tags": tags}
        if scanned % PROGRESS_EVERY_WAYS == 0:
            progress(f"OSM scan 0%: selecting backbone and candidate connector roads "
                     f"({scanned:,} ways scanned; {selected:,} selected)")
    paired_added = _add_paired_backbone_carriageways(data, paired_carriageways)
    if paired_added:
        progress(f"Added {paired_added:,} unnumbered paired backbone carriageways")
    progress("OSM scan 25%: expanding public roads at motorway exits")
    # A local street at an exit is useful even without an N-number. Limit these additions to ways
    # that physically meet a motorway_link, so this does not pull Belgium's whole street network.
    slip_nodes = {node for way in data.ways.values()
                  if way["tags"].get("highway") == "motorway_link" for node in way["nodes"]}
    access_added = []
    frontier = slip_nodes
    for hop in range(EXIT_ACCESS_HOPS):
        next_frontier = set()
        added = 0
        for scanned, way in enumerate(osmium.FileProcessor(str(extract), osmium.osm.WAY), 1):
            tags = dict(way.tags)
            nodes = [node.ref for node in way.nodes]
            if hop == 0:
                for node in set(nodes) & slip_nodes:
                    data.access_classes.setdefault(node, set()).add(tags.get("highway", ""))
            if exit_access_candidate(tags) and way.id not in data.ways and set(nodes) & frontier:
                data.ways[way.id] = {"nodes": nodes, "tags": tags}
                next_frontier.update(nodes)
                added += 1
            if scanned % PROGRESS_EVERY_WAYS == 0:
                progress(f"OSM scan {25 + hop * 25}%: expanding public roads at motorway exits "
                         f"(hop {hop + 1}/{EXIT_ACCESS_HOPS}; {scanned:,} ways scanned; {added:,} added)")
        access_added.append(added)
        frontier = next_frontier
        progress(f"OSM scan {50 + hop * 25}%: completed exit-access expansion {hop + 1}/{EXIT_ACCESS_HOPS}")
        if not frontier:
            break
    progress(f"{len(data.ways):,} road ways selected "
             f"({paired_added:,} paired carriageways; {access_added[0]:,} direct and "
             f"{sum(access_added[1:]):,} extended exit-access ways)")
    needed = {node for way in data.ways.values() for node in way["nodes"]}
    progress("OSM scan 75%: reading coordinates for selected roads")
    for scanned, node in enumerate(osmium.FileProcessor(str(extract), osmium.osm.NODE).with_filter(osmium.filter.IdFilter(needed)), 1):
        data.points[node.id] = (node.location.lat, node.location.lon)
        if node.tags.get("highway") == "motorway_junction":
            data.junctions[node.id] = dict(node.tags)
        if scanned % PROGRESS_EVERY_NODES == 0:
            progress(f"OSM scan 75%: reading coordinates for selected roads "
                     f"({scanned:,}/{len(needed):,} points read)")
    progress(f"{len(data.points):,} points read, {len(data.junctions):,} motorway junctions")
    progress("OSM scan 100%: source cache ready")
    partial = cache.with_suffix(".partial")
    with partial.open("w", encoding="utf-8") as file:
        json.dump({"extract_stamp": stamp, "selection_version": SELECTION_VERSION,
                   "ways": data.ways, "points": data.points,
                   "junctions": data.junctions,
                   "access_classes": {node: sorted(classes) for node, classes in data.access_classes.items()},
                   "paired_carriageways": sorted(data.paired_carriageways)},
                  file, separators=(",", ":"))
    partial.replace(cache)
    return data
