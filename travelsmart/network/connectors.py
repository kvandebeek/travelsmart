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


def select_network_ways(data: OsmData, progress=print, update=None) -> OsmData:
    """Keep the backbone plus shortest legal local-road paths in both directions.

    Candidate local ways come from the extract cache. Outbound and inbound paths may use different
    ways around one-way streets, so both are selected before building the network graph.
    """
    backbone = {way_id for way_id, way in data.ways.items() if wanted(way["tags"])} | data.paired_carriageways
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
    way_count = len(data.ways)
    for index, (way_id, way) in enumerate(data.ways.items(), 1):
        if way["tags"].get("highway") not in MOTORWAY:
            regional.add_way(way_id, way["nodes"], way["tags"], data.points)
        if update and (index == way_count or index % 500 == 0):
            update(0.35 * index / way_count, f"indexing local roads ({index:,}/{way_count:,})")

    def shortest_paths(adjacency, start_fraction, direction):
        best = {node: 0.0 for node in targets}
        previous = {}
        queue = [(0.0, node) for node in targets]
        heapq.heapify(queue)
        remaining = starts - targets
        total_starts = len(remaining)
        popped = 0
        while queue and remaining:
            distance, node = heapq.heappop(queue)
            popped += 1
            if distance != best.get(node) or distance > MAX_CONNECTOR_METRES:
                continue
            remaining.discard(node)
            for following, way_id, length in adjacency.get(node, ()):
                candidate = distance + length
                if candidate <= MAX_CONNECTOR_METRES and candidate < best.get(following, math.inf):
                    best[following] = candidate
                    previous[following] = (node, way_id)
                    heapq.heappush(queue, (candidate, following))
            if update and popped % 5_000 == 0:
                reached = total_starts - len(remaining)
                fraction = start_fraction + 0.30 * reached / max(total_starts, 1)
                update(fraction, f"finding {direction}-backbone connector paths "
                                 f"({reached:,}/{total_starts:,} exits reached; {popped:,} nodes searched)")
        return best, previous

    connectors = set()
    reached = []
    for direction, adjacency in enumerate((regional.out, regional.inn), 1):
        direction_name = ("to", "from")[direction - 1]
        best, previous = shortest_paths(adjacency, 0.35 + 0.30 * (direction - 1), direction_name)
        reached.append(len((starts - targets) & best.keys()))
        for start in (starts - targets) & best.keys():
            node = start
            while node not in targets:
                node, way_id = previous[node]
                connectors.add(way_id)
        if update:
            update(0.35 + 0.30 * direction,
                   f"found {direction_name}-backbone connector paths ({direction}/2)")
    selected = backbone | connectors
    progress(f"{len(connectors - backbone):,} local connector ways selected; "
             f"{reached[1]:,}/{len(starts - targets):,} local motorway junctions can reach the "
             f"backbone and {reached[0]:,} can be reached from it within "
             f"{MAX_CONNECTOR_METRES // 1000} km")
    if update:
        update(1.0, "connector selection complete")
    return OsmData(ways={way_id: data.ways[way_id] for way_id in selected},
                   points=data.points, junctions=data.junctions,
                   access_classes=data.access_classes,
                   paired_carriageways=data.paired_carriageways & selected)
