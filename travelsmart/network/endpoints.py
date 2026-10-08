"""Endpoints where trips start or end, and the access edges that hang them off the network (§3.3, §4).

Endpoints are not part of the measured backbone: each one reaches the network through a short access
edge to its one or two nearest network nodes. Endpoints that reach the network at the same nodes form
one **cluster** with one set of access edges, so thousands of schools or stations do not each add
their own edges to measure.

Access paths follow real roads, in both directions separately: a station reached through a one-way
street leaves by another, so the two directions have their own path and length.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from travelsmart.network.build import Graph, Grid, _ordered_numbers, metres, walk
from travelsmart.network.osm import MOTORWAY, OsmData

# A rough box around Belgium. It is a cheap guard, not a border: the country's south-eastern corner
# and Luxembourg share this rectangle, so anything needing a real answer uses BELGIAN_UIC below.
BELGIUM_BOUNDS = (49.40, 2.50, 51.60, 6.45)
# The SNCB feed also lists stops in Luxembourg, France, Germany and the Netherlands. Their station
# codes carry the UIC country code, which separates them exactly where a bounding box cannot.
BELGIAN_UIC = "88"
# A landuse polygon this small is a workshop or a corner shop, not a destination worth measuring.
MIN_ZONE_HECTARES = 2.0
OSM_ENDPOINT_VERSION = 3
SNAP_METRES = 2_000         # an endpoint further than this from a selected road gets no access edge
MAX_ACCESS_METRES = 5_000   # §4: access edges are 0.5-5 km
ACCESS_NODES = 2            # §4: an endpoint cluster links to its 1-2 nearest network nodes


@dataclass
class Endpoint:
    """A place trips start or end: a station, business park, school, ..."""
    id: str
    category: str
    name: str
    lat: float
    lon: float
    weight: float = 0.0


@dataclass
class Endpoints:
    endpoints: dict[str, dict] = field(default_factory=dict)   # id -> {category, name, lat, lon, weight, cluster}
    clusters: dict[str, dict] = field(default_factory=dict)    # id -> {lat, lon, nodes, endpoints, categories}
    edges: dict[str, dict] = field(default_factory=dict)       # id -> {from, to, kind, roads, metres, path}
    unreachable: list[str] = field(default_factory=list)       # endpoints with no network node in reach
    estimated: list[str] = field(default_factory=list)         # endpoints linked by distance, not by road


def in_belgium(lat: float, lon: float) -> bool:
    south, west, north, east = BELGIUM_BOUNDS
    return south <= lat <= north and west <= lon <= east


def read_gtfs_stations(rows) -> list[Endpoint]:
    """Stations from GTFS stops rows (csv.DictReader over stops.txt).

    A GTFS feed lists every platform as its own stop. Only the parent station is an endpoint, or the
    stop itself where a feed models no parents at all; otherwise one station becomes a dozen
    endpoints a few metres apart.
    """
    stations = []
    for row in rows:
        location_type = (row.get("location_type") or "").strip()
        if location_type not in ("", "0", "1"):       # entrances, nodes and boarding areas are not places
            continue
        if location_type != "1" and (row.get("parent_station") or "").strip():
            continue                                   # a platform of a station that is itself listed
        stop_id = (row.get("stop_id") or "").strip()
        try:
            lat, lon = float(row["stop_lat"]), float(row["stop_lon"])
        except (KeyError, TypeError, ValueError):
            continue
        # A UIC code says the country exactly; anything else falls back on where the stop actually is.
        # The SNCB feed writes a station's own code with an "S" prefix (S8814001) and its platforms
        # as plain codes, and lists foreign stops (Hamburg, London) as plain stops with no parent.
        code = stop_id.split("_")[0].lstrip("S")
        if code.isdigit() and len(code) >= 7:
            if not code.startswith(BELGIAN_UIC):
                continue
        elif not in_belgium(lat, lon):
            continue
        stations.append(Endpoint(id=f"station:{stop_id}", category="station",
                                 name=(row.get("stop_name") or "").strip(), lat=lat, lon=lon))
    return stations


def endpoint_category(tags: dict) -> str | None:
    """The endpoint category an OpenStreetMap feature belongs to, or None (§3.3).

    Stations come from the railway's own feed; everything here is read from the extract that already
    builds the roads, which covers the whole country in one format. The official registers per region
    carry the weights (pupils, beds, jobs) and can enrich these later; this is where they are.
    """
    if tags.get("amenity") == "school":
        return "school"
    if tags.get("amenity") == "hospital":
        return "hospital"
    if tags.get("office") == "government" or tags.get("amenity") == "townhall":
        return "government"
    if tags.get("amenity") == "parking" and tags.get("park_ride", "no") != "no":
        return "park_ride"
    if tags.get("aeroway") == "aerodrome":
        return "airport"
    if tags.get("landuse") in ("industrial", "commercial"):
        return "business_park"
    if tags.get("shop") == "mall" or tags.get("landuse") == "retail":
        return "retail"
    return None


ZONE_CATEGORIES = {"business_park", "retail", "airport"}   # judged by area, so only as a polygon
# §3.3 weights an endpoint by how much traffic it draws: beds, pupils, jobs. The official registers
# hold those, but OpenStreetMap carries some of them already, and reading them here costs nothing.
# A zone has no such count and is weighted by its area in hectares instead.
WEIGHT_TAGS = ("beds", "capacity:persons", "capacity")


def endpoint_weight(tags: dict) -> float:
    """What this place is worth as a destination, from whichever count OpenStreetMap carries."""
    for key in WEIGHT_TAGS:
        value = (tags.get(key) or "").strip()
        if value.isdigit():
            return float(value)
    return 0.0


def _hectares(ring: list[tuple[float, float]]) -> float:
    """Area of a closed ring of (lat, lon), projected to rough metres at Belgium's latitude."""
    points = [(lon * 70_000, lat * 111_200) for lat, lon in ring]
    twice = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:] + points[:1]))
    return abs(twice) / 2 / 10_000


def _centroid(ring: list[tuple[float, float]]) -> tuple[float, float]:
    return sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)


DUPLICATE_METRES = 100


def deduplicate(endpoints: list[Endpoint]) -> list[Endpoint]:
    """Drop the second copy of a place mapped both as a point and as an outline.

    OpenStreetMap often carries a school as a node *and* as the polygon around it. Both land within a
    few dozen metres, so without this one school becomes two endpoints. Two places of the same kind
    that close together are only treated as one when their names agree, or one of them has no name.
    """
    kept: list[Endpoint] = []
    grid = Grid()
    # The outline carries the area, so it is the copy worth keeping: offer those first.
    for endpoint in sorted(endpoints, key=lambda e: (-e.weight, e.id)):
        point = (endpoint.lat, endpoint.lon)
        duplicate = False
        for other, where in grid.near(point, DUPLICATE_METRES):
            if (other.category == endpoint.category and metres(point, where) <= DUPLICATE_METRES
                    and (not other.name or not endpoint.name or other.name == endpoint.name)):
                duplicate = True
                break
        if not duplicate:
            kept.append(endpoint)
            grid.add(endpoint, point)
    return kept


def load_osm_endpoints(extract, progress=print) -> list[Endpoint]:
    """Schools, hospitals, government offices, park-and-ride, business parks and large attractors.

    Two passes keep memory low, as in osm.py: first the matching nodes and the ways worth keeping,
    then only the coordinates those ways use. The result is cached beside the extract, because this
    walks the whole country and the answer only changes when the extract does.
    """
    import json
    from pathlib import Path

    import osmium

    extract = Path(extract)
    cache = extract.with_suffix(".endpoints.json")
    stamp = [extract.stat().st_size, extract.stat().st_mtime_ns]
    if cache.exists():
        saved = json.loads(cache.read_text(encoding="utf-8"))
        if saved.get("extract_stamp") == stamp and saved.get("version") == OSM_ENDPOINT_VERSION:
            progress(f"Using cached OpenStreetMap endpoints from {cache}")
            return [Endpoint(**row) for row in saved["endpoints"]]

    progress("endpoint scan 0%: reading places from the extract")
    found: list[Endpoint] = []
    areas: dict[int, tuple[str, dict, list[int]]] = {}
    for scanned, obj in enumerate(osmium.FileProcessor(str(extract), osmium.osm.NODE | osmium.osm.WAY), 1):
        tags = dict(obj.tags)
        category = endpoint_category(tags)
        if category:
            if isinstance(obj, osmium.osm.Node):
                if category not in ZONE_CATEGORIES and in_belgium(obj.location.lat, obj.location.lon):
                    found.append(Endpoint(id=f"{category}:n{obj.id}", category=category,
                                          name=(tags.get("name") or "").strip(),
                                          lat=round(obj.location.lat, 7), lon=round(obj.location.lon, 7),
                                          weight=endpoint_weight(tags)))
            else:
                areas[obj.id] = (category, tags, [node.ref for node in obj.nodes])
        if scanned % 1_000_000 == 0:
            progress(f"endpoint scan 0%: reading places from the extract "
                     f"({scanned:,} objects; {len(found):,} points, {len(areas):,} areas)")

    needed = {node for _, _, refs in areas.values() for node in refs}
    progress(f"endpoint scan 50%: reading coordinates for {len(areas):,} areas")
    points: dict[int, tuple[float, float]] = {}
    for node in osmium.FileProcessor(str(extract), osmium.osm.NODE).with_filter(osmium.filter.IdFilter(needed)):
        points[node.id] = (node.location.lat, node.location.lon)

    for way_id, (category, tags, refs) in areas.items():
        ring = [points[ref] for ref in refs if ref in points]
        if len(ring) < 3:
            continue
        hectares = _hectares(ring)
        if category in ZONE_CATEGORIES and hectares < MIN_ZONE_HECTARES:
            continue
        lat, lon = _centroid(ring)
        if not in_belgium(lat, lon):
            continue
        found.append(Endpoint(id=f"{category}:w{way_id}", category=category,
                              name=(tags.get("name") or "").strip(), lat=round(lat, 7), lon=round(lon, 7),
                              weight=round(hectares, 2) if category in ZONE_CATEGORIES
                              else endpoint_weight(tags)))
    before = len(found)
    found = deduplicate(found)
    progress(f"endpoint scan 100%: {len(found):,} places read "
             f"({before - len(found):,} duplicate points inside their own outline dropped)")

    partial = cache.with_suffix(".partial")
    partial.write_text(json.dumps({"extract_stamp": stamp, "version": OSM_ENDPOINT_VERSION,
                                   "endpoints": [vars(e) for e in found]}, separators=(",", ":")),
                       encoding="utf-8")
    partial.replace(cache)
    return found


class _Reversed:
    """The graph with every edge turned around, so walk() finds the paths *into* its start node."""

    def __init__(self, graph: Graph):
        self.out = graph.inn


def _cluster_id(nodes: tuple[str, ...]) -> str:
    return "p" + hashlib.sha1("|".join(nodes).encode()).hexdigest()[:10]


def _edge_id(start: str, end: str) -> str:
    return "a" + hashlib.sha1(f"access|{start}|{end}".encode()).hexdigest()[:10]


def _nearest_network_nodes(graph, start: int, node_of_osm: dict[int, str], limit: int):
    """The closest distinct network nodes reachable from start, as (node id, metres, path, ways)."""
    found = walk(graph, start, lambda osm: osm in node_of_osm, limit_metres=limit)
    best: dict[str, tuple[float, list[int], list[int]]] = {}
    for osm, (distance, path, ways) in found.items():
        node_id = node_of_osm[osm]
        if node_id not in best or distance < best[node_id][0]:
            best[node_id] = (distance, path, ways)
    return sorted(((node_id, *rest) for node_id, rest in best.items()), key=lambda item: item[1])[:ACCESS_NODES]


def build_endpoints(endpoints: list[Endpoint], nodes: dict[str, dict], data: OsmData,
                    update=None) -> Endpoints:
    """Snap endpoints to the road network and group those that reach it at the same nodes."""
    points = data.points
    node_of_osm = {osm: node_id for node_id, node in nodes.items() for osm in node.get("osm_nodes", ())}

    local = Graph()
    for way_id, way in data.ways.items():
        if way["tags"].get("highway") not in MOTORWAY:     # a trip never joins a motorway directly
            local.add_way(way_id, way["nodes"], way["tags"], points)
    reversed_local = _Reversed(local)

    grid = Grid()
    graph_nodes = local.nodes()
    for osm in graph_nodes:
        if osm in points:
            grid.add(osm, points[osm])

    # Only the backbone and its connectors are selected, so an endpoint on an ordinary street often
    # has no road path to a network node. Those fall back on the nearest nodes by distance, marked
    # "estimated" so a later pass over the full local street network can replace them.
    node_grid = Grid()
    for node_id, node in nodes.items():
        if "lat" in node and "lon" in node:
            node_grid.add(node_id, (node["lat"], node["lon"]))

    result = Endpoints()
    members: dict[tuple[str, ...], list[Endpoint]] = {}
    # cluster key -> node id -> direction -> (metres, osm path, ways), the shortest seen for the cluster
    links: dict[tuple[str, ...], dict[str, dict[str, tuple[float, list[int], list[int]]]]] = {}
    for index, endpoint in enumerate(endpoints, 1):
        point = (endpoint.lat, endpoint.lon)
        nearest = min(grid.near(point, SNAP_METRES), key=lambda item: metres(point, item[1]), default=None)
        to_network = _nearest_network_nodes(local, nearest[0], node_of_osm, MAX_ACCESS_METRES) if nearest else []
        if to_network:
            # The way back can use other streets: it is searched separately and keeps its own length.
            from_network = {node_id: (distance, list(reversed(path)), list(reversed(ways)))
                            for node_id, distance, path, ways
                            in _nearest_network_nodes(reversed_local, nearest[0], node_of_osm, MAX_ACCESS_METRES)}
            key = tuple(sorted(node_id for node_id, _, _, _ in to_network))
            per_node = links.setdefault(key, {})
            for node_id, distance, path, ways in to_network:
                directions = per_node.setdefault(node_id, {})
                # One cluster measures one set of access edges: keep the shortest path for each node.
                if "out" not in directions or distance < directions["out"][0]:
                    directions["out"] = (distance, path, ways, False)
                inbound = from_network.get(node_id)
                if inbound and ("in" not in directions or inbound[0] < directions["in"][0]):
                    directions["in"] = (*inbound, False)
        else:
            close = sorted(((node_id, metres(point, where)) for node_id, where
                            in node_grid.near(point, MAX_ACCESS_METRES)), key=lambda item: item[1])
            if not close:
                result.unreachable.append(endpoint.id)
                continue
            result.estimated.append(endpoint.id)
            key = tuple(sorted(node_id for node_id, _ in close[:ACCESS_NODES]))
            per_node = links.setdefault(key, {})
            for node_id, distance in close[:ACCESS_NODES]:
                directions = per_node.setdefault(node_id, {})
                if "out" not in directions or distance < directions["out"][0]:
                    directions["out"] = (distance, [], [], True)
                    directions["in"] = (distance, [], [], True)
        members.setdefault(key, []).append(endpoint)
        if update and (index == len(endpoints) or index % 50 == 0):
            update(0.9 * index / len(endpoints), f"linking endpoints to the network ({index:,}/{len(endpoints):,})")

    for key, grouped in members.items():
        cluster_id = _cluster_id(key)
        result.clusters[cluster_id] = {
            "lat": round(sum(e.lat for e in grouped) / len(grouped), 6),
            "lon": round(sum(e.lon for e in grouped) / len(grouped), 6),
            "nodes": list(key),
            "endpoints": sorted(e.id for e in grouped),
            "categories": sorted({e.category for e in grouped}),
            "weight": round(sum(e.weight for e in grouped), 3),
        }
        for endpoint in grouped:
            result.endpoints[endpoint.id] = {"category": endpoint.category, "name": endpoint.name,
                                             "lat": endpoint.lat, "lon": endpoint.lon,
                                             "weight": endpoint.weight, "cluster": cluster_id}
        for node_id, directions in links[key].items():
            for direction, (distance, osm_path, ways, estimated) in directions.items():
                start, end = (cluster_id, node_id) if direction == "out" else (node_id, cluster_id)
                path = [[round(points[osm][0], 6), round(points[osm][1], 6)]
                        for osm in osm_path if osm in points]
                if estimated:   # a straight line between the cluster and the node, not a driven road
                    node = nodes[node_id]
                    path = [[result.clusters[cluster_id]["lat"], result.clusters[cluster_id]["lon"]],
                            [node["lat"], node["lon"]]]
                    if direction == "in":
                        path.reverse()
                result.edges[_edge_id(start, end)] = {
                    "from": start, "to": end, "kind": "access",
                    "roads": _ordered_numbers(ways, data),
                    "metres": round(distance), "estimated": estimated, "path": path,
                }
    if update:
        update(1.0, f"{len(result.clusters):,} endpoint clusters, {len(result.edges):,} access edges")
    return result
