"""Build the network's nodes and edges from OpenStreetMap road data (docs/network-design.md §3-4).

Node kinds:
  motorway    a point on a motorway carriageway where an on-ramp merges in (one per direction)
  connection  where slip roads meet the local road (an exit), or where a motorway starts or ends;
              merged with the N-road node there when one is close by
  regional    where different N-roads meet, or an N-road ends; roundabouts and dual-carriageway
              crossings count as one node
  split       an extra point that keeps every edge at most MAX_EDGE_METRES long
Edge kinds: motorway (along one carriageway), transfer (across an interchange), ramp_on, ramp_off,
ramp_link, junction (zero-distance turn at a shared OSM node), regional. Every measured edge has
its own start and end point on the carriageway it uses, so a measurement
from start to end drives exactly that stretch, in that direction.
"""

from __future__ import annotations

import hashlib
import heapq
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field

from travelsmart.network.osm import OsmData

MAX_EDGE_METRES = 6000          # longer stretches get split nodes
REGIONAL_CLUSTER_METRES = 120   # one node per roundabout / dual-carriageway crossing
CONNECTION_CLUSTER_METRES = 500  # the slip roads of one exit
SNAP_METRES = 120               # an exit joins an N-road node this close
REGIONAL_REF = re.compile(r"^[NR]\d{1,2}[a-z]?$")
ROAD_NUMBER = re.compile(r"^[AENR]\d+[a-z]?$")


def metres(a: tuple[float, float], b: tuple[float, float]) -> float:
    r = math.pi / 180
    x = (math.sin((b[0] - a[0]) * r / 2) ** 2
         + math.cos(a[0] * r) * math.cos(b[0] * r) * math.sin((b[1] - a[1]) * r / 2) ** 2)
    return 12_742_000 * math.asin(math.sqrt(x))


def road_numbers(tags: dict) -> list[str]:
    """Road numbers of a way: "E 40;A 10" plus int_ref -> ["E40", "A10"]."""
    numbers = []
    for key in ("ref", "int_ref"):
        for part in tags.get(key, "").split(";"):
            number = part.replace(" ", "").strip()
            if ROAD_NUMBER.match(number) and number not in numbers:
                numbers.append(number)
    return numbers


def travel_directions(tags: dict) -> tuple[bool, bool]:
    """(forward allowed, backward allowed) along the way's node order."""
    oneway = tags.get("oneway", "")
    if oneway in ("-1", "reverse"):
        return False, True
    if oneway == "no":
        return True, True
    implied = tags.get("highway") in ("motorway", "motorway_link") or tags.get("junction") in ("roundabout", "circular")
    if oneway in ("yes", "true", "1") or implied:
        return True, False
    return True, True


class Grid:
    """Points in ~500 m cells, for "what is near here" questions."""
    CELL = 0.005

    def __init__(self):
        self.cells: dict[tuple[int, int], list] = defaultdict(list)

    def add(self, key, point):
        self.cells[int(point[0] / self.CELL), int(point[1] / self.CELL)].append((key, point))

    def near(self, point, radius_metres):
        reach = int(radius_metres / 350) + 1
        row, col = int(point[0] / self.CELL), int(point[1] / self.CELL)
        for dr in range(-reach, reach + 1):
            for dc in range(-reach, reach + 1):
                for key, other in self.cells.get((row + dr, col + dc), ()):
                    if metres(point, other) <= radius_metres:
                        yield key, other


class UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, item):
        self.parent.setdefault(item, item)
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


@dataclass
class Graph:
    """Directed road graph over OSM nodes: node -> [(next node, way id, metres)]."""
    out: dict[int, list] = field(default_factory=lambda: defaultdict(list))
    inn: dict[int, list] = field(default_factory=lambda: defaultdict(list))

    def add_way(self, way_id: int, nodes: list[int], tags: dict, points: dict) -> None:
        forward, backward = travel_directions(tags)
        for a, b in zip(nodes, nodes[1:]):
            if a not in points or b not in points:
                continue
            length = metres(points[a], points[b])
            if forward:
                self.out[a].append((b, way_id, length))
                self.inn[b].append((a, way_id, length))
            if backward:
                self.out[b].append((a, way_id, length))
                self.inn[a].append((b, way_id, length))

    def nodes(self):
        return set(self.out) | set(self.inn)


def walk(graph: Graph, start: int, is_stop, limit_metres: float = 80_000) -> dict[int, tuple[float, list[int], list[int]]]:
    """Shortest paths from start to every first-reached stop node: stop -> (metres, nodes, ways).

    Paths do not continue through stops, so each result is a stretch between neighbouring stops.
    """
    best = {start: 0.0}
    previous: dict[int, tuple[int, int]] = {}
    found = {}
    queue = [(0.0, start)]
    while queue:
        distance, node = heapq.heappop(queue)
        if distance > best.get(node, math.inf) or distance > limit_metres:
            continue
        if node != start and is_stop(node):
            path, ways, cursor = [node], [], node
            while cursor != start:
                cursor, way = previous[cursor]
                path.append(cursor)
                ways.append(way)
            found[node] = (distance, path[::-1], ways[::-1])
            continue
        for following, way, length in graph.out.get(node, ()):
            candidate = distance + length
            if candidate < best.get(following, math.inf):
                best[following] = candidate
                previous[following] = (node, way)
                heapq.heappush(queue, (candidate, following))
    return found


@dataclass
class Network:
    nodes: dict[str, dict] = field(default_factory=dict)     # id -> {kind, lat, lon, roads, name}
    edges: dict[str, dict] = field(default_factory=dict)     # id -> {from, to, kind, roads, metres, path}


def _edge_id(kind: str, start: str, end: str) -> str:
    return kind[0] + hashlib.sha1(f"{kind}|{start}|{end}".encode()).hexdigest()[:10]


def _ordered_numbers(ways: list[int], data: OsmData) -> list[str]:
    numbers = []
    for way in ways:
        for number in road_numbers(data.ways[way]["tags"]):
            if number not in numbers:
                numbers.append(number)
    return numbers


def build_network(data: OsmData, update=None) -> Network:
    points = data.points
    motorway, regional = Graph(), Graph()
    regional_refs: dict[int, set] = defaultdict(set)
    motorway_ways = set()
    way_count = len(data.ways)
    for index, (way_id, way) in enumerate(data.ways.items(), 1):
        tags, nodes = way["tags"], way["nodes"]
        highway = tags.get("highway", "")
        if highway in ("motorway", "motorway_link"):
            motorway.add_way(way_id, nodes, tags, points)
            if highway == "motorway":
                motorway_ways.add(way_id)
        else:
            regional.add_way(way_id, nodes, tags, points)
            label = frozenset(r for r in road_numbers(tags) if REGIONAL_REF.match(r)) or frozenset({"roundabout"})
            for node in nodes:
                regional_refs[node].add(label)
        if update and (index == way_count or index % 500 == 0):
            update(0.20 * index / way_count, f"indexing road graph ({index:,}/{way_count:,})")

    # --- motorways ------------------------------------------------------------------------------
    on_motorway = {node for way in motorway_ways for node in data.ways[way]["nodes"]}
    motorway_nodes = motorway.nodes()
    shared_junctions = motorway_nodes & regional.nodes()  # the same OSM node, not a nearby crossing
    entries = {n for n in motorway_nodes if not motorway.inn.get(n) and motorway.out.get(n)}
    exits = {n for n in motorway_nodes if motorway.out.get(n) is None or not motorway.out.get(n)}
    exits &= {n for n in motorway_nodes if motorway.inn.get(n)}
    # carriageway nodes: where a slip road (or a starting motorway) joins a motorway carriageway
    merges = {node for node in on_motorway
              if any(way not in motorway_ways for _, way, _ in motorway.inn.get(node, ()))
              and any(way in motorway_ways for _, way, _ in motorway.out.get(node, ()))}
    merges |= {node for node in on_motorway if node in entries}
    # A regional road can meet a motorway carriageway at its start/end. Keep a carriageway node
    # there for through traffic and a separate connection node for turns onto the regional road.
    merges |= shared_junctions & on_motorway

    # --- connection nodes: slip-road ends near each other form one exit -----------------------------
    groups = UnionFind()
    grid = Grid()
    connection_ends = list(entries | exits)
    for index, node in enumerate(connection_ends, 1):
        groups.find(("c", node))
        # A slip end at a real OSM junction already connects to a regional road. Keep each such
        # junction distinct: clustering two ends can erase the local road between them.
        if node in shared_junctions:
            continue
        for other, _ in grid.near(points[node], CONNECTION_CLUSTER_METRES):
            groups.union(("c", node), ("c", other))
        grid.add(node, points[node])
        if update and (index == len(connection_ends) or index % 100 == 0):
            update(0.20 + 0.05 * index / len(connection_ends),
                   f"grouping motorway exits ({index:,}/{len(connection_ends):,})")
    for node in shared_junctions:
        groups.find(("c", node))
        groups.union(("c", node), ("r", node))

    # --- regional key nodes ----------------------------------------------------------------------
    regional_nodes = regional.nodes()
    regional_node_list = list(regional_nodes)
    neighbours = {}
    for index, node in enumerate(regional_node_list, 1):
        neighbours[node] = ({n for n, _, _ in regional.out.get(node, ())} |
                            {n for n, _, _ in regional.inn.get(node, ())})
        if update and (index == len(regional_node_list) or index % 1_000 == 0):
            update(0.25 + 0.03 * index / len(regional_node_list),
                   f"finding regional key junctions ({index:,}/{len(regional_node_list):,})")
    keys = {node for node in regional_nodes if len(regional_refs[node]) > 1 or len(neighbours[node]) == 1}
    keys |= shared_junctions
    regional_grid = Grid()
    for index, node in enumerate(regional_node_list, 1):
        regional_grid.add(node, points[node])
        if update and (index == len(regional_node_list) or index % 1_000 == 0):
            update(0.28 + 0.03 * index / len(regional_node_list),
                   f"indexing regional junctions ({index:,}/{len(regional_node_list):,})")
    snap_ends = list((entries | exits) - shared_junctions)
    for index, node in enumerate(snap_ends, 1):  # exact shared junctions are already joined
        nearest = min(regional_grid.near(points[node], SNAP_METRES), key=lambda item: metres(points[node], item[1]), default=None)
        if nearest:
            keys.add(nearest[0])
            groups.union(("c", node), ("r", nearest[0]))
        if update and (index == len(snap_ends) or index % 100 == 0):
            update(0.31 + 0.03 * index / len(snap_ends),
                   f"matching exits to regional roads ({index:,}/{len(snap_ends):,})")
    key_grid = Grid()
    key_nodes = list(keys)
    for index, node in enumerate(key_nodes, 1):
        groups.find(("r", node))
        # Shared motorway/local junctions are exact turns. Merging even nearby ones would erase
        # the intervening local road and its one-way restriction.
        if node in shared_junctions:
            continue
        for other, _ in key_grid.near(points[node], REGIONAL_CLUSTER_METRES):
            groups.union(("r", node), ("r", other))
        key_grid.add(node, points[node])
        if update and (index == len(key_nodes) or index % 250 == 0):
            update(0.34 + 0.02 * index / len(key_nodes),
                   f"grouping regional junctions ({index:,}/{len(key_nodes):,})")

    network = Network()
    members: dict[tuple, list[tuple]] = defaultdict(list)
    group_items = list(groups.parent)
    for index, item in enumerate(group_items, 1):
        members[groups.find(item)].append(item)
        if update and (index == len(group_items) or index % 500 == 0):
            update(0.36 + 0.02 * index / len(group_items),
                   f"assembling logical junctions ({index:,}/{len(group_items):,})")

    def logical(kind_char: str, node: int) -> str:
        root = groups.find((kind_char, node))
        return ("c" if any(m[0] == "c" for m in members[root]) else "r") + str(root[1])

    member_items = list(members.items())
    for index, (root, items) in enumerate(member_items, 1):
        node_id = ("c" if any(m[0] == "c" for m in items) else "r") + str(root[1])
        lat = sum(points[m[1]][0] for m in items) / len(items)
        lon = sum(points[m[1]][1] for m in items) / len(items)
        refs = sorted({r for m in items if m[0] == "r" for label in regional_refs[m[1]] for r in label if r != "roundabout"})
        network.nodes[node_id] = {"kind": "connection" if node_id[0] == "c" else "regional",
                                  "lat": round(lat, 6), "lon": round(lon, 6), "roads": refs, "name": "",
                                  "osm_nodes": sorted({member[1] for member in items}),
                                  "access_classes": sorted({road_class for member in items
                                                            for road_class in data.access_classes.get(member[1], ())})}
        if update and (index == len(member_items) or index % 250 == 0):
            update(0.38 + 0.02 * index / len(member_items),
                   f"creating network nodes ({index:,}/{len(member_items):,})")
    for node in merges:
        network.nodes[f"m{node}"] = {"kind": "motorway", "lat": points[node][0], "lon": points[node][1],
                                     "roads": _ordered_numbers([w for _, w, _ in motorway.out[node] if w in motorway_ways], data),
                                     "name": "", "osm_nodes": [node]}
    _name_connections(network, data, update=update)
    if update:
        update(0.40, f"created {len(network.nodes):,} network nodes")

    # --- edges ------------------------------------------------------------------------------------
    candidates: dict[tuple[str, str, str], tuple] = {}

    def keep(kind, start_id, end_id, distance, path, ways):
        if start_id == end_id:
            return
        key = (kind, start_id, end_id)
        if key not in candidates or distance < candidates[key][0]:
            candidates[key] = (distance, path, ways)

    motor_stops = merges | entries | exits | shared_junctions
    stop_motorway = lambda node: node in motor_stops
    motorway_stops = list(motor_stops)
    for index, start in enumerate(motorway_stops, 1):
        for end, (distance, path, ways) in walk(motorway, start, stop_motorway).items():
            start_is_motorway, end_is_motorway = start in merges, end in merges
            start_id = f"m{start}" if start_is_motorway else logical("c", start)
            end_id = f"m{end}" if end_is_motorway else logical("c", end)
            if start_is_motorway and end_is_motorway:
                kind = "motorway" if all(way in motorway_ways for way in ways) else "transfer"
            elif start_is_motorway:
                kind = "ramp_off"
            elif end_is_motorway:
                kind = "ramp_on"
            else:
                kind = "ramp_link"
            keep(kind, start_id, end_id, distance, path, ways)
        if update and (index == len(motorway_stops) or index % 50 == 0):
            update(0.40 + 0.25 * index / len(motorway_stops),
                   f"tracing motorway links ({index:,}/{len(motorway_stops):,})")
    stop_regional = lambda node: node in keys
    regional_keys = list(keys)
    for index, start in enumerate(regional_keys, 1):
        for end, (distance, path, ways) in walk(regional, start, stop_regional, limit_metres=60_000).items():
            keep("regional", logical("r", start), logical("r", end), distance, path, ways)
        if update and (index == len(regional_keys) or index % 50 == 0):
            update(0.65 + 0.20 * index / len(regional_keys),
                   f"tracing regional links ({index:,}/{len(regional_keys):,})")

    candidate_items = list(candidates.items())
    for index, ((kind, start_id, end_id), (distance, path, ways)) in enumerate(candidate_items, 1):
        # a transfer edge also names the motorway it joins ("E40" then "E19")
        joins = [w for _, w, _ in motorway.out.get(path[-1], ()) if w in motorway_ways] if kind == "transfer" else []
        _add_split(network, data, kind, start_id, end_id, path, ways, joins)
        if update and (index == len(candidate_items) or index % 100 == 0):
            update(0.85 + 0.15 * index / len(candidate_items),
                   f"creating route segments ({index:,}/{len(candidate_items):,})")
    # At a shared OSM junction on a motorway carriageway, the physical point serves two roles.
    # These zero-distance directed turns connect its motorway node to its regional connection node.
    for osm in shared_junctions & on_motorway:
        motor_id, connection_id = f"m{osm}", logical("c", osm)
        point = [round(points[osm][0], 6), round(points[osm][1], 6)]
        for start_id, end_id, allowed in ((motor_id, connection_id, bool(motorway.inn.get(osm))),
                                           (connection_id, motor_id, bool(motorway.out.get(osm)))):
            if allowed:
                edge_id = _edge_id("junction", start_id, end_id)
                network.edges[edge_id] = {"from": start_id, "to": end_id, "kind": "junction",
                                          "roads": sorted(set(network.nodes[motor_id]["roads"]) |
                                                          set(network.nodes[connection_id]["roads"])),
                                          "metres": 0, "start": point, "end": point, "path": [point, point]}
    if update:
        update(1.0, f"built {len(network.edges):,} directed edges")
    return network


def _name_connections(network: Network, data: OsmData, update=None) -> None:
    """Name each exit after the nearest motorway-junction tag ("Antwerpen-Centrum (5)")."""
    grid = Grid()
    for node, tags in data.junctions.items():
        if node in data.points and (tags.get("name") or tags.get("ref")):
            grid.add(node, data.points[node])
    network_nodes = list(network.nodes.values())
    for index, node in enumerate(network_nodes, 1):
        if node["kind"] != "connection":
            continue
        point = (node["lat"], node["lon"])
        nearest = min(grid.near(point, 1500), key=lambda item: metres(point, item[1]), default=None)
        if nearest:
            tags = data.junctions[nearest[0]]
            node["name"] = " ".join(filter(None, [tags.get("name"), f"({tags['ref']})" if tags.get("ref") else None]))
        if update and (index == len(network_nodes) or index % 250 == 0):
            update(0.39 + 0.01 * index / len(network_nodes),
                   f"naming exit connections ({index:,}/{len(network_nodes):,})")


def _add_split(network: Network, data: OsmData, kind: str, start_id: str, end_id: str,
               path: list[int], ways: list[int], joins: list[int] = ()) -> None:
    """Add an edge, split into equal parts no longer than MAX_EDGE_METRES.

    Split points are chosen from the end with the lower node id, so both directions of a two-way
    road share them.
    """
    points = data.points
    lengths = [0.0]
    for a, b in zip(path, path[1:]):
        lengths.append(lengths[-1] + metres(points[a], points[b]))
    total = lengths[-1]
    pieces = max(1, math.ceil(total / MAX_EDGE_METRES))
    inner = set()
    if pieces > 1 and len(path) > 2:
        flip = start_id > end_id
        for piece in range(1, pieces):
            target = total * piece / pieces
            target = total - target if flip else target
            inner.add(min(range(1, len(path) - 1), key=lambda i: abs(lengths[i] - target)))
    # The nearest OSM vertex can leave one piece just over the cap. Refine any such gap.
    while True:
        cuts = [0, *sorted(inner), len(path) - 1]
        long_gap = next(((a, b) for a, b in zip(cuts, cuts[1:])
                         if lengths[b] - lengths[a] > MAX_EDGE_METRES), None)
        if long_gap is None:
            break
        first, last = long_gap
        if last - first < 2:
            raise ValueError(f"OSM segment {path[first]} → {path[last]} exceeds {MAX_EDGE_METRES} m")
        midpoint = (lengths[first] + lengths[last]) / 2
        inner.add(min(range(first + 1, last), key=lambda i: abs(lengths[i] - midpoint)))
    cuts = [0, *sorted(inner), len(path) - 1]
    for first, last in zip(cuts, cuts[1:]):
        a = start_id if first == 0 else f"s{path[first]}"
        b = end_id if last == len(path) - 1 else f"s{path[last]}"
        for node_id, osm in ((a, path[first]), (b, path[last])):
            if node_id.startswith("s") and node_id not in network.nodes:
                network.nodes[node_id] = {"kind": "split", "lat": points[osm][0], "lon": points[osm][1],
                                          "roads": _ordered_numbers(ways[max(first - 1, 0):last], data), "name": "",
                                          "osm_nodes": [osm]}
        piece_ways = ways[first:last] + (list(joins) if last == len(path) - 1 else [])
        edge_id = _edge_id(kind, a, b)
        network.edges[edge_id] = {
            "from": a, "to": b, "kind": kind, "roads": _ordered_numbers(piece_ways, data),
            "metres": round(lengths[last] - lengths[first]),
            "start": [round(points[path[first]][0], 6), round(points[path[first]][1], 6)],
            "end": [round(points[path[last]][0], 6), round(points[path[last]][1], 6)],
            "path": [points[n] for n in path[first:last + 1]],
        }
