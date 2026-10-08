"""Find likely topology mistakes in a generated road network.

The checks flag candidates for review; nearby roads may cross at different levels and must not be
joined automatically. Coordinates are projected to approximate metres at Belgium's latitude.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict, deque

CELL_METRES = 250
SEARCH_METRES = 250
BACKBONE_REF = re.compile(r"^[NR]\s?\d{1,2}[a-z]?$", re.IGNORECASE)
MOTORWAY_EDGE_KINDS = {"ramp_on", "ramp_off", "ramp_link", "motorway", "transfer", "junction"}
PUBLIC_EXIT_ROADS = {"trunk", "trunk_link", "primary", "primary_link", "secondary", "secondary_link",
                     "tertiary", "tertiary_link", "unclassified", "residential"}


def _xy(lat: float, lon: float) -> tuple[float, float]:
    return lon * 70_000, lat * 111_200


def _distance_to_segment(point, a, b) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length_squared = dx * dx + dy * dy
    if not length_squared:
        return math.dist(point, a)
    t = max(0.0, min(1.0, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length_squared))
    return math.dist(point, (a[0] + t * dx, a[1] + t * dy))


def _reachable(starts: set[str], adjacency: dict[str, set[str]]) -> set[str]:
    reached = set(starts)
    queue = deque(starts)
    while queue:
        for following in adjacency.get(queue.popleft(), ()):
            if following not in reached:
                reached.add(following)
                queue.append(following)
    return reached


def audit_network(nodes: dict, edges: dict, source_ways: dict | None = None,
                  source_points: dict | None = None, update=None) -> dict:
    """Report isolated nodes, small components and dead ends close to an unjoined road."""
    neighbours = {node_id: set() for node_id in nodes}
    incident_kinds = defaultdict(set)
    incoming_kinds, outgoing_kinds = defaultdict(set), defaultdict(set)
    regional_out, regional_in, regional_neighbours = defaultdict(set), defaultdict(set), defaultdict(set)
    backbone_anchors = set()
    edge_values = list(edges.values())
    for index, edge in enumerate(edge_values, 1):
        neighbours[edge["from"]].add(edge["to"])
        neighbours[edge["to"]].add(edge["from"])
        incident_kinds[edge["from"]].add(edge["kind"])
        incident_kinds[edge["to"]].add(edge["kind"])
        outgoing_kinds[edge["from"]].add(edge["kind"])
        incoming_kinds[edge["to"]].add(edge["kind"])
        if edge["kind"] == "regional":
            start, end = edge["from"], edge["to"]
            regional_out[start].add(end)
            regional_in[end].add(start)
            regional_neighbours[start].add(end)
            regional_neighbours[end].add(start)
            if any(BACKBONE_REF.fullmatch(road) for road in edge["roads"]):
                backbone_anchors.update((start, end))
        if update and (index == len(edge_values) or index % 500 == 0):
            update(0.15 * index / len(edge_values), f"auditing graph links ({index:,}/{len(edge_values):,})")

    components = []
    component_of = {}
    node_ids = list(nodes)
    for index, node_id in enumerate(node_ids, 1):
        if node_id in component_of:
            continue
        stack = [node_id]
        component = set()
        while stack:
            node = stack.pop()
            if node in component:
                continue
            component.add(node)
            stack.extend(neighbours[node] - component)
        index = len(components)
        components.append(component)
        component_of.update({node: index for node in component})
        if update and (index == len(node_ids) or index % 500 == 0):
            update(0.15 + 0.15 * index / len(node_ids),
                   f"auditing connected components ({index:,}/{len(node_ids):,})")
    components.sort(key=len, reverse=True)
    component_of = {node: index for index, members in enumerate(components) for node in members}

    # Index road segments in 250 m cells. Query only cells touching a dangling node's 100 m radius.
    segments = []
    cells = defaultdict(list)
    edge_items = list(edges.items())
    for edge_index, (edge_id, edge) in enumerate(edge_items, 1):
        path = [_xy(*point) for point in edge["path"]]
        for a, b in zip(path, path[1:]):
            segment_index = len(segments)
            segments.append((edge_id, a, b))
            left, right = sorted((int(a[0] // CELL_METRES), int(b[0] // CELL_METRES)))
            bottom, top = sorted((int(a[1] // CELL_METRES), int(b[1] // CELL_METRES)))
            for col in range(left, right + 1):
                for row in range(bottom, top + 1):
                    cells[col, row].append(segment_index)
        if update and (edge_index == len(edge_items) or edge_index % 500 == 0):
            update(0.30 + 0.20 * edge_index / len(edge_items),
                   f"indexing road segments for audit ({edge_index:,}/{len(edge_items):,})")

    issues = []
    undirected_backbone = _reachable(backbone_anchors, regional_neighbours)
    from_backbone = _reachable(backbone_anchors, regional_out)
    to_backbone = _reachable(backbone_anchors, regional_in)
    exit_connections_checked = 0
    exit_connections_without_backbone_path = 0
    exit_to_backbone_direction_gaps = 0
    backbone_to_entry_direction_gaps = 0
    for index, (node_id, node) in enumerate(nodes.items(), 1):
        if update and (index == len(nodes) or index % 500 == 0):
            update(0.50 + 0.10 * index / len(nodes),
                   f"checking exit connectivity ({index:,}/{len(nodes):,})")
        if node["kind"] != "connection":
            continue
        needs_exit = "ramp_off" in incoming_kinds[node_id]
        needs_entry = "ramp_on" in outgoing_kinds[node_id]
        if not (needs_exit or needs_entry):
            continue
        # When present, OSM's direct road classes distinguish a public interchange from a service
        # area or a motorway-only turn. Old/synthetic data without the field stays auditable.
        if "access_classes" in node and not (set(node["access_classes"]) & PUBLIC_EXIT_ROADS):
            continue
        exit_connections_checked += 1
        if node_id not in undirected_backbone:
            category = "no_backbone_path"
            missing = []
            exit_connections_without_backbone_path += 1
        else:
            missing = []
            if needs_exit and node_id not in to_backbone:
                missing.append("exit_to_backbone")
                exit_to_backbone_direction_gaps += 1
            if needs_entry and node_id not in from_backbone:
                missing.append("backbone_to_entry")
                backbone_to_entry_direction_gaps += 1
            if not missing:
                continue
            category = "directional_backbone_gap"
        issues.append({
            "category": category, "node": node_id, "kind": node["kind"],
            "lat": node["lat"], "lon": node["lon"], "roads": node["roads"],
            "degree": len(neighbours[node_id]), "component": component_of[node_id],
            "component_nodes": len(components[component_of[node_id]]),
            "missing_directions": missing, "nearest": None,
        })
    for index, (node_id, node) in enumerate(nodes.items(), 1):
        if update and (index == len(nodes) or index % 500 == 0):
            update(0.60 + 0.15 * index / len(nodes),
                   f"checking road-end gaps ({index:,}/{len(nodes):,})")
        degree = len(neighbours[node_id])
        if degree > 1:
            continue
        point = _xy(node["lat"], node["lon"])
        col, row = int(point[0] // CELL_METRES), int(point[1] // CELL_METRES)
        candidates = {}
        for x in range(col - 1, col + 2):
            for y in range(row - 1, row + 2):
                for index in cells.get((x, y), ()):
                    edge_id, a, b = segments[index]
                    edge = edges[edge_id]
                    if node_id in (edge["from"], edge["to"]):
                        continue
                    distance = _distance_to_segment(point, a, b)
                    if distance <= SEARCH_METRES and distance < candidates.get(edge_id, math.inf):
                        candidates[edge_id] = distance
        options = []
        for edge_id, distance in candidates.items():
            edge = edges[edge_id]
            options.append({
                "edge": edge_id,
                "metres": round(distance, 1),
                "same_road": bool(set(node["roads"]) & set(edge["roads"])),
                "other_component": component_of[node_id] != component_of[edge["from"]],
                "roads": edge["roads"],
            })
        options.sort(key=lambda option: (not option["other_component"],
                                          not option["same_road"], option["metres"]))
        possible_gaps = [option for option in options
                         if option["other_component"] and option["metres"] <= 100 or
                         option["same_road"] and option["metres"] <= 200]
        best = possible_gaps[0] if possible_gaps else (options[0] if options else None)
        component = component_of[node_id]
        if degree == 0:
            category = "isolated_node"
        elif possible_gaps:
            category = "possible_gap"
        elif component != 0:
            category = "small_component_end"
        else:
            continue
        issues.append({
            "category": category,
            "node": node_id,
            "kind": node["kind"],
            "lat": node["lat"], "lon": node["lon"],
            "roads": node["roads"],
            "degree": degree,
            "component": component,
            "component_nodes": len(components[component]),
            "nearest": best,
        })
    source_shared = set()
    unrepresented = 0
    unrepresented_far = 0
    if source_ways is not None:
        motorway_source, regional_source = set(), set()
        backbone_refs = defaultdict(set)
        source_way_values = list(source_ways.values())
        for index, way in enumerate(source_way_values, 1):
            is_motorway = way["tags"].get("highway") in ("motorway", "motorway_link")
            target = motorway_source if is_motorway else regional_source
            target.update(way["nodes"])
            if not is_motorway:
                refs = {ref.strip().replace(" ", "") for ref in way["tags"].get("ref", "").split(";")
                        if BACKBONE_REF.match(ref.strip())}
                for osm in way["nodes"]:
                    backbone_refs[osm].update(refs)
            if update and (index == len(source_way_values) or index % 500 == 0):
                update(0.75 + 0.10 * index / len(source_way_values),
                       f"checking source-road coverage ({index:,}/{len(source_way_values):,})")
        source_shared = motorway_source & regional_source
        represented_by = defaultdict(set)
        for index, (node_id, node) in enumerate(nodes.items(), 1):
            for osm in node.get("osm_nodes", ()):
                represented_by[osm].add(node_id)
            if update and (index == len(nodes) or index % 500 == 0):
                update(0.85 + 0.10 * index / len(nodes),
                       f"matching source junctions ({index:,}/{len(nodes):,})")
        represented = set(represented_by)
        unrepresented = len(source_shared - represented)
        joined_points = [_xy(node["lat"], node["lon"]) for node_id, node in nodes.items()
                         if "regional" in incident_kinds[node_id] and
                         incident_kinds[node_id] & MOTORWAY_EDGE_KINDS]
        if source_points is not None:
            for osm in source_shared - represented:
                point = source_points.get(osm, source_points.get(str(osm)))
                if point is None:
                    continue
                xy = _xy(*point)
                if joined_points and min(math.dist(xy, joined) for joined in joined_points) <= 500:
                    continue
                unrepresented_far += 1
                issues.append({
                    "category": "unrepresented_source_junction", "node": f"osm{osm}",
                    "kind": "source_junction", "lat": point[0], "lon": point[1],
                    "roads": [], "degree": 0, "component": None, "component_nodes": 0,
                    "source_osm_nodes": [osm], "nearest": None,
                })
        missing_by_node = defaultdict(lambda: {"osm": [], "refs": set()})
        for osm in source_shared & represented:
            graph_nodes = represented_by[osm]
            if any("regional" in incident_kinds[node_id] and
                   incident_kinds[node_id] & MOTORWAY_EDGE_KINDS for node_id in graph_nodes):
                continue
            # Prefer the regional side of the source junction as the map marker.
            node_id = min(graph_nodes, key=lambda value: ("regional" not in incident_kinds[value],
                                                          nodes[value]["kind"] != "connection", value))
            missing_by_node[node_id]["osm"].append(osm)
            missing_by_node[node_id]["refs"].update(backbone_refs[osm])
        for node_id, missing in missing_by_node.items():
            node = nodes[node_id]
            component = component_of[node_id]
            refs = sorted(missing["refs"])
            issues.append({
                "category": "source_junction_missing_link" if refs else "local_connector_needed",
                "node": node_id,
                "kind": node["kind"], "lat": node["lat"], "lon": node["lon"],
                "roads": node["roads"], "degree": len(neighbours[node_id]),
                "component": component, "component_nodes": len(components[component]),
                "source_osm_nodes": sorted(missing["osm"]), "source_backbone_roads": refs,
                "nearest": None,
            })
    rank = {"source_junction_missing_link": 0, "unrepresented_source_junction": 1,
            "local_connector_needed": 2, "directional_backbone_gap": 3,
            "no_backbone_path": 4, "possible_gap": 5,
            "isolated_node": 6, "small_component_end": 7}
    issues.sort(key=lambda item: (rank[item["category"]],
                                  not (item["nearest"] or {}).get("other_component", False),
                                  not (item["nearest"] or {}).get("same_road", False),
                                  (item["nearest"] or {}).get("metres", math.inf)))
    if update:
        update(1.0, "audit complete")
    return {
        "summary": {
            "nodes": len(nodes), "edges": len(edges),
            "components": len(components), "largest_component_nodes": len(components[0]),
            "isolated_nodes": sum(not adjacent for adjacent in neighbours.values()),
            "dead_ends": sum(len(adjacent) == 1 for adjacent in neighbours.values()),
            "possible_gaps": sum(issue["category"] == "possible_gap" for issue in issues),
            "source_junctions_missing_link": sum(issue["category"] == "source_junction_missing_link"
                                                 for issue in issues),
            "local_connectors_needed": sum(issue["category"] == "local_connector_needed"
                                           for issue in issues),
            "source_junctions_unrepresented": unrepresented,
            "source_junctions_unrepresented_far": unrepresented_far,
            "source_shared_junctions": len(source_shared),
            "exit_connections_checked": exit_connections_checked,
            "exit_connections_without_backbone_path": exit_connections_without_backbone_path,
            "exit_to_backbone_direction_gaps": exit_to_backbone_direction_gaps,
            "backbone_to_entry_direction_gaps": backbone_to_entry_direction_gaps,
        },
        "issues": issues,
    }
