"""Select local roads that connect motorway exits to the numbered backbone."""

from __future__ import annotations

import heapq
import math
from travelsmart.network.build import Graph
from travelsmart.network.osm import MAIN_N_ROAD, MOTORWAY, RING_ROAD, OsmData, wanted

# Some valid motorway exits join a three-digit regional road before reaching the main numbered
# backbone. Fifteen kilometres covers those documented connector corridors without selecting the
# wider local street network.
MAX_CONNECTOR_METRES = 15_000


def select_network_ways(data: OsmData, progress=print) -> OsmData:
    """Keep the backbone plus shortest legal local-road paths in both directions.

    Candidate local ways come from the extract cache. Outbound and inbound paths may use different
    ways around one-way streets, so both are selected before building the network graph.
    """
    backbone = {way_id for way_id, way in data.ways.items() if wanted(way["tags"])}
    main = {way_id for way_id in backbone if data.ways[way_id]["tags"].get("highway") not in MOTORWAY
            and (MAIN_N_ROAD.search(data.ways[way_id]["tags"].get("ref", ""))
                 or RING_ROAD.search(data.ways[way_id]["tags"].get("ref", "")))}
    motorway_nodes = {node for way in data.ways.values() if way["tags"].get("highway") in MOTORWAY
                      for node in way["nodes"]}
    local_nodes = {node for way in data.ways.values() if way["tags"].get("highway") not in MOTORWAY
                   for node in way["nodes"]}
    starts = motorway_nodes & local_nodes
    targets = {node for way_id in main for node in data.ways[way_id]["nodes"]}

    regional = Graph()
    for way_id, way in data.ways.items():
        if way["tags"].get("highway") in MOTORWAY:
            continue
        regional.add_way(way_id, way["nodes"], way["tags"], data.points)

    def shortest_paths(adjacency):
        best = {node: 0.0 for node in targets}
        previous = {}
        queue = [(0.0, node) for node in targets]
        heapq.heapify(queue)
        remaining = starts - targets
        while queue and remaining:
            distance, node = heapq.heappop(queue)
            if distance != best.get(node) or distance > MAX_CONNECTOR_METRES:
                continue
            remaining.discard(node)
            for following, way_id, length in adjacency.get(node, ()):
                candidate = distance + length
                if candidate <= MAX_CONNECTOR_METRES and candidate < best.get(following, math.inf):
                    best[following] = candidate
                    previous[following] = (node, way_id)
                    heapq.heappush(queue, (candidate, following))
        return best, previous

    connectors = set()
    reached = []
    for adjacency in (regional.out, regional.inn):
        best, previous = shortest_paths(adjacency)
        reached.append(len((starts - targets) & best.keys()))
        for start in (starts - targets) & best.keys():
            node = start
            while node not in targets:
                node, way_id = previous[node]
                connectors.add(way_id)
    selected = backbone | connectors
    progress(f"{len(connectors - backbone):,} local connector ways selected; "
             f"{reached[1]:,}/{len(starts - targets):,} local motorway junctions can reach the "
             f"backbone and {reached[0]:,} can be reached from it within "
             f"{MAX_CONNECTOR_METRES // 1000} km")
    return OsmData(ways={way_id: data.ways[way_id] for way_id in selected},
                   points=data.points, junctions=data.junctions,
                   access_classes=data.access_classes)
