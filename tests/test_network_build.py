"""The network builder on small hand-made road layouts (coordinates near 51 N, 5 E)."""

from travelsmart.network.build import MAX_EDGE_METRES, Network, _add_split, build_network, metres
from travelsmart.network.connectors import select_network_ways
from travelsmart.network.osm import EXIT_ACCESS_HOPS, OsmData, connector_candidate, exit_access_candidate, wanted

LAT, LON = 51.0, 5.0
KM_LON = 1 / 70.0      # ~1 km east at this latitude
KM_LAT = 1 / 111.2     # ~1 km north


def layout(ways: list[tuple[int, list[int], dict]], points: dict[int, tuple[float, float]], junctions=None) -> OsmData:
    data = OsmData()
    data.points = {node: (LAT + y * KM_LAT, LON + x * KM_LON) for node, (x, y) in points.items()}
    data.ways = {way_id: {"nodes": nodes, "tags": tags} for way_id, nodes, tags in ways}
    data.junctions = junctions or {}
    return data


def edges_of(network, kind):
    return sorted((e["from"], e["to"]) for e in network.edges.values() if e["kind"] == kind)


def test_motorway_exit_gives_carriageway_nodes_ramps_and_a_connection():
    # Eastbound carriageway 1 -> 2 -> 3 -> 4 -> 5 (km 0..4). An off-ramp leaves at 2 to the local road
    # at 20; an on-ramp from 21, 300 m further along that road, merges at 4. The motorway starts at 1.
    data = layout(
        [(1, [1, 2, 3, 4, 5], {"highway": "motorway", "ref": "A2", "int_ref": "E 314"}),
         (2, [2, 20], {"highway": "motorway_link"}),
         (3, [21, 4], {"highway": "motorway_link"})],
        {1: (0, 0), 2: (1, 0), 3: (2, 0), 4: (3, 0), 5: (4, 0), 20: (1.5, 0.4), 21: (1.8, 0.4)},
        junctions={2: {"highway": "motorway_junction", "name": "Genk-Oost", "ref": "31"}})
    network = build_network(data)
    kinds = {node["kind"] for node in network.nodes.values()}
    assert {"motorway", "connection"} <= kinds
    assert ("m1", "m4") in edges_of(network, "motorway")                    # carriageway: start -> merge
    exit_node = next(e["to"] for e in network.edges.values() if e["kind"] == "ramp_off")
    assert network.nodes[exit_node]["kind"] == "connection" and network.nodes[exit_node]["name"] == "Genk-Oost (31)"
    assert [e["from"] for e in network.edges.values() if e["kind"] == "ramp_on"] == [exit_node]   # one exit node
    stretch = next(e for e in network.edges.values() if (e["from"], e["to"]) == ("m1", "m4"))
    assert stretch["roads"] == ["A2", "E314"] and abs(stretch["metres"] - 3000) < 60


def test_interchange_link_is_a_transfer_edge():
    # Motorway A eastbound 1 -> 2 -> 3; at 2 a link leaves to motorway B (northbound 10 -> 11 -> 12),
    # merging at 11. Both motorways start at their first node.
    data = layout(
        [(1, [1, 2, 3], {"highway": "motorway", "ref": "E40"}),
         (2, [10, 11, 12], {"highway": "motorway", "ref": "E19"}),
         (3, [2, 11], {"highway": "motorway_link"})],
        {1: (0, 0), 2: (1, 0), 3: (2, 0), 10: (1.5, -1), 11: (1.5, 0.5), 12: (1.5, 1.5)})
    network = build_network(data)
    assert ("m1", "m11") in edges_of(network, "transfer")
    transfer = next(e for e in network.edges.values() if e["kind"] == "transfer")
    assert transfer["roads"] == ["E40", "E19"]


def test_regional_roads_meet_at_one_node_per_roundabout_and_long_stretches_are_split():
    # N2 west-east, N74 south-north. They meet at a small roundabout (5-6-7-8). East of it the N2
    # runs 14 km with a point every 100 m (as OpenStreetMap roads do), so that stretch is split in three.
    roundabout = {"highway": "primary", "junction": "roundabout"}
    east = list(range(100, 241))                      # nodes 100..240: 0.0 .. 14.0 km east
    data = layout(
        [(1, [1, 5], {"highway": "primary", "ref": "N2"}),
         (2, [7] + east, {"highway": "primary", "ref": "N2"}),
         (3, [10, 6], {"highway": "secondary", "ref": "N74"}),
         (4, [8, 13], {"highway": "secondary", "ref": "N74"}),
         (5, [5, 6, 7, 8, 5], roundabout)],
        {1: (-3, 0), 5: (-0.03, 0), 6: (0, -0.03), 7: (0.03, 0), 8: (0, 0.03), 10: (0, -4), 13: (0, 4),
         **{node: (0.1 + (node - 100) * 0.1, 0) for node in east}})
    network = build_network(data)
    roundabout_nodes = {n for n, node in network.nodes.items() if node["kind"] == "regional" and node["roads"] == ["N2", "N74"]}
    assert len(roundabout_nodes) == 1
    centre = roundabout_nodes.pop()
    east = [e for e in network.edges.values() if e["kind"] == "regional" and e["roads"] == ["N2"] and e["metres"] > 3000]
    assert len(east) == 6                                   # 14 km east of the roundabout, both directions, 3 parts each
    assert all(e["metres"] <= MAX_EDGE_METRES for e in network.edges.values())
    splits = {e["to"] for e in east if e["to"].startswith("s")} | {e["from"] for e in east if e["from"].startswith("s")}
    assert len(splits) == 2                                 # shared by both directions
    assert any(e["from"] == centre and e["roads"] == ["N74"] for e in network.edges.values())


def test_exit_on_an_n_road_joins_its_node():
    # A motorway exit whose slip road ends on the N74: the exit and the N-road crossing are one node.
    data = layout(
        [(1, [1, 2, 3], {"highway": "motorway", "ref": "E314"}),
         (2, [2, 20], {"highway": "motorway_link"}),
         (3, [30, 20, 31], {"highway": "secondary", "ref": "N74"})],
        {1: (0, 0), 2: (1, 0), 3: (2, 0), 20: (1.5, 0.5), 30: (1.5, -3), 31: (1.5, 4)})
    network = build_network(data)
    exit_node = next(e["to"] for e in network.edges.values() if e["kind"] == "ramp_off")
    assert any(e["from"] == exit_node and e["kind"] == "regional" for e in network.edges.values())
    assert network.nodes[exit_node]["kind"] == "connection" and network.nodes[exit_node]["roads"] == ["N74"]


def test_one_way_regional_stretch_only_has_its_own_direction():
    data = layout(
        [(1, [1, 2], {"highway": "primary", "ref": "N9", "oneway": "yes"})],
        {1: (0, 0), 2: (2, 0)})
    network = build_network(data)
    assert len(network.edges) == 1
    edge = next(iter(network.edges.values()))
    assert metres(tuple(edge["start"]), (LAT, LON)) < 1


def test_split_refines_a_cut_when_osm_vertices_are_uneven():
    # A nearest-vertex split at 6.2 km used to leave an edge above the 6 km cap.
    data = layout([(1, [1, 2, 3, 4], {"highway": "primary", "ref": "N2"})],
                  {1: (0, 0), 2: (3, 0), 3: (6.2, 0), 4: (12, 0)})
    network = Network()
    _add_split(network, data, "regional", "r1", "r4", [1, 2, 3, 4], [1, 1, 1])
    assert len(network.edges) == 3
    assert max(edge["metres"] for edge in network.edges.values()) <= MAX_EDGE_METRES


def test_city_ring_roads_are_included_in_regional_backbone():
    assert wanted({"highway": "primary", "ref": "R40"})
    assert wanted({"highway": "secondary", "ref": "R71"})
    assert wanted({"highway": "tertiary", "ref": "N9"})
    assert not wanted({"highway": "secondary", "ref": "N120"})


def test_shared_osm_junction_on_carriageway_keeps_through_road_and_regional_turns():
    data = layout(
        [(1, [1, 2, 3], {"highway": "motorway", "ref": "R0"}),
         (2, [10, 2, 11], {"highway": "primary", "ref": "N3"})],
        {1: (0, 0), 2: (1, 0), 3: (2, 0), 10: (1, -1), 11: (1, 1)})
    network = build_network(data)
    assert ("m1", "m2") in edges_of(network, "motorway")
    assert ("m2", "c2") in edges_of(network, "junction")
    assert ("c2", "m2") in edges_of(network, "junction")
    assert any(e["from"] == "c2" and e["kind"] == "regional" for e in network.edges.values())


def test_shared_osm_junction_on_slip_road_connects_to_regional_road():
    data = layout(
        [(1, [1, 2, 3, 4], {"highway": "motorway", "ref": "E40"}),
         (2, [2, 20, 3], {"highway": "motorway_link"}),
         (3, [30, 20, 31], {"highway": "primary", "ref": "N3"})],
        {1: (0, 0), 2: (1, 0), 3: (3, 0), 4: (4, 0),
         20: (2, 0.5), 30: (2, -1), 31: (2, 1)})
    network = build_network(data)
    assert ("m1", "c20") in edges_of(network, "ramp_off")
    assert ("c20", "m3") in edges_of(network, "ramp_on")
    assert any(e["from"] == "c20" and e["kind"] == "regional" for e in network.edges.values())


def test_short_road_between_two_exit_junctions_keeps_its_length():
    data = layout(
        [(1, [1, 2, 3, 4], {"highway": "motorway", "ref": "E40"}),
         (2, [2, 20], {"highway": "motorway_link"}),
         (3, [21, 3], {"highway": "motorway_link"}),
         (4, [20, 21], {"highway": "tertiary", "oneway": "yes"})],
        {1: (0, 0), 2: (1, 0), 3: (3, 0), 4: (4, 0),
         20: (1.5, 0.5), 21: (1.58, 0.5)})
    network = build_network(data)
    assert network.nodes["c20"]["kind"] == "connection"
    assert network.nodes["c21"]["kind"] == "connection"
    assert ("c20", "c21") in edges_of(network, "regional")
    assert ("c21", "c20") not in edges_of(network, "regional")
    assert next(e["metres"] for e in network.edges.values()
                if e["kind"] == "regional" and e["from"] == "c20") > 50


def test_only_local_roads_needed_by_an_exit_are_selected():
    data = layout(
        [(1, [1, 2], {"highway": "primary", "ref": "N2"}),
         (2, [2, 3, 4], {"highway": "secondary", "ref": "N200"}),
         (3, [5, 4], {"highway": "motorway_link"}),
         (4, [10, 11], {"highway": "secondary", "ref": "N300"})],
        {1: (0, 0), 2: (1, 0), 3: (2, 0), 4: (3, 0), 5: (4, 0),
         10: (10, 0), 11: (11, 0)})
    selected = select_network_ways(data, progress=lambda _: None)
    assert set(selected.ways) == {1, 2, 3}


def test_connector_selection_keeps_separate_one_way_arrival_and_departure():
    data = layout(
        [(1, [1, 2], {"highway": "primary", "ref": "N2"}),
         (2, [2, 3], {"highway": "tertiary", "oneway": "yes"}),
         (3, [3, 4], {"highway": "tertiary", "oneway": "yes"}),
         (4, [4, 5], {"highway": "tertiary", "oneway": "yes"}),
         (5, [5, 2], {"highway": "tertiary", "oneway": "yes"}),
         (6, [6, 4], {"highway": "motorway_link"}),
         (7, [10, 11], {"highway": "tertiary"})],
        {1: (0, 0), 2: (1, 0), 3: (2, 0), 4: (3, 0), 5: (2, 1), 6: (4, 0),
         10: (10, 0), 11: (11, 0)})
    selected = select_network_ways(data, progress=lambda _: None)
    assert set(selected.ways) == {1, 2, 3, 4, 5, 6}


def test_connector_candidates_exclude_private_roads():
    assert connector_candidate({"highway": "tertiary", "access": "yes"})
    assert connector_candidate({"highway": "secondary", "ref": "N200"})
    assert not connector_candidate({"highway": "tertiary", "motor_vehicle": "private"})
    assert not connector_candidate({"highway": "secondary", "access": "no"})


def test_exit_access_candidates_are_limited_to_public_small_roads():
    assert EXIT_ACCESS_HOPS == 2
    assert exit_access_candidate({"highway": "unclassified"})
    assert exit_access_candidate({"highway": "residential", "access": "destination"})
    assert not exit_access_candidate({"highway": "service"})
    assert not exit_access_candidate({"highway": "residential", "access": "private"})
