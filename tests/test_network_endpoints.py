"""Endpoints snap to the network, share a cluster and get an access edge per direction."""

from travelsmart.network.endpoints import (Endpoint, build_endpoints, in_belgium,
                                           read_gtfs_stations)
from travelsmart.network.osm import OsmData

LAT, LON = 51.0, 5.0
KM_LON = 1 / 70.0
KM_LAT = 1 / 111.2


def layout(ways, points):
    data = OsmData()
    data.points = {node: (LAT + y * KM_LAT, LON + x * KM_LON) for node, (x, y) in points.items()}
    data.ways = {way_id: {"nodes": nodes, "tags": tags} for way_id, nodes, tags in ways}
    return data


def at(x, y):
    return LAT + y * KM_LAT, LON + x * KM_LON


def test_endpoints_reaching_the_same_nodes_share_one_cluster():
    # An N2 between two network nodes, with a side street at 1 km that two stations sit on.
    data = layout(
        [(1, [1, 2, 3], {"highway": "primary", "ref": "N2"}),
         (2, [2, 10], {"highway": "residential"})],
        {1: (0, 0), 2: (1, 0), 3: (2, 0), 10: (1, 0.3)})
    nodes = {"r1": {"osm_nodes": [1]}, "r3": {"osm_nodes": [3]}}
    first, second = at(1, 0.3), at(1, 0.31)
    result = build_endpoints(
        [Endpoint("station:a", "station", "A", first[0], first[1]),
         Endpoint("station:b", "station", "B", second[0], second[1])],
        nodes, data)
    assert len(result.clusters) == 1
    cluster = next(iter(result.clusters.values()))
    assert cluster["endpoints"] == ["station:a", "station:b"]
    assert sorted(cluster["nodes"]) == ["r1", "r3"]
    assert {e["cluster"] for e in result.endpoints.values()} == set(result.clusters)


def test_access_edges_run_in_both_directions_with_real_lengths():
    data = layout(
        [(1, [1, 2, 3], {"highway": "primary", "ref": "N2"}),
         (2, [2, 10], {"highway": "residential"})],
        {1: (0, 0), 2: (1, 0), 3: (2, 0), 10: (1, 0.3)})
    nodes = {"r1": {"osm_nodes": [1]}, "r3": {"osm_nodes": [3]}}
    here = at(1, 0.3)
    result = build_endpoints([Endpoint("station:a", "station", "A", here[0], here[1])], nodes, data)
    cluster = next(iter(result.clusters))
    pairs = {(edge["from"], edge["to"]) for edge in result.edges.values()}
    assert (cluster, "r1") in pairs and ("r1", cluster) in pairs
    assert (cluster, "r3") in pairs and ("r3", cluster) in pairs
    out = next(e for e in result.edges.values() if (e["from"], e["to"]) == (cluster, "r1"))
    assert abs(out["metres"] - 1300) < 120           # 300 m side street plus 1 km of N2
    assert out["roads"] == ["N2"] and len(out["path"]) >= 3


def test_a_one_way_street_gives_the_return_trip_its_own_path():
    # The station sits on a one-way loop: out over 10 -> 11 -> 3, back over 1 -> 2 -> 10.
    data = layout(
        [(1, [1, 2, 3], {"highway": "primary", "ref": "N2"}),
         (2, [2, 10], {"highway": "residential", "oneway": "yes"}),
         (3, [10, 11, 3], {"highway": "residential", "oneway": "yes"})],
        {1: (0, 0), 2: (1, 0), 3: (3, 0), 10: (1, 0.3), 11: (2, 0.3)})
    nodes = {"r1": {"osm_nodes": [1]}, "r3": {"osm_nodes": [3]}}
    here = at(1, 0.3)
    result = build_endpoints([Endpoint("station:a", "station", "A", here[0], here[1])], nodes, data)
    cluster = next(iter(result.clusters))
    outbound = next(e for e in result.edges.values() if (e["from"], e["to"]) == (cluster, "r3"))
    inbound = next(e for e in result.edges.values() if (e["from"], e["to"]) == ("r3", cluster))
    assert outbound["path"][0] != inbound["path"][0]     # they leave from different ends
    assert inbound["path"][-1] == outbound["path"][0]    # and both touch the station's street


def test_an_endpoint_far_from_every_road_is_reported_not_linked():
    data = layout([(1, [1, 2], {"highway": "primary", "ref": "N2"})], {1: (0, 0), 2: (1, 0)})
    nodes = {"r1": {"osm_nodes": [1], "lat": LAT, "lon": LON},
             "r2": {"osm_nodes": [2], "lat": LAT, "lon": LON + KM_LON}}
    far = at(60, 60)
    result = build_endpoints([Endpoint("station:far", "station", "Far", far[0], far[1])], nodes, data)
    assert result.unreachable == ["station:far"]
    assert not result.clusters and not result.edges


def test_an_endpoint_off_the_selected_roads_falls_back_on_the_nearest_nodes_by_distance():
    # Only the backbone is selected, so a station on an ordinary street has no road path to a node.
    # It still gets access edges, marked estimated so a later pass can replace them with real paths.
    data = layout([(1, [1, 2], {"highway": "primary", "ref": "N2"})], {1: (0, 0), 2: (1, 0)})
    nodes = {"r1": {"osm_nodes": [1], "lat": LAT, "lon": LON},
             "r2": {"osm_nodes": [2], "lat": at(1, 0)[0], "lon": at(1, 0)[1]}}
    here = at(0.5, 2.5)       # 2.5 km off the road: beyond SNAP_METRES, inside MAX_ACCESS_METRES
    result = build_endpoints([Endpoint("station:a", "station", "A", here[0], here[1])], nodes, data)
    assert result.estimated == ["station:a"] and not result.unreachable
    assert len(result.clusters) == 1
    assert result.edges and all(edge["estimated"] for edge in result.edges.values())
    cluster = next(iter(result.clusters))
    pairs = {(edge["from"], edge["to"]) for edge in result.edges.values()}
    assert (cluster, "r1") in pairs and ("r1", cluster) in pairs


def test_gtfs_stations_keep_parents_and_drop_platforms_and_foreign_stops():
    rows = [
        {"stop_id": "8814001", "stop_name": "Brussel-Zuid", "stop_lat": "50.835707",
         "stop_lon": "4.336531", "location_type": "1", "parent_station": ""},
        {"stop_id": "8814001_7", "stop_name": "Brussel-Zuid platform 7", "stop_lat": "50.835707",
         "stop_lon": "4.336531", "location_type": "0", "parent_station": "8814001"},
        {"stop_id": "8400058", "stop_name": "Amsterdam", "stop_lat": "52.378", "stop_lon": "4.900",
         "location_type": "1", "parent_station": ""},
        {"stop_id": "8891009", "stop_name": "Gent-Sint-Pieters", "stop_lat": "51.036", "stop_lon": "3.711",
         "location_type": "", "parent_station": ""},
    ]
    stations = read_gtfs_stations(rows)
    assert [s.id for s in stations] == ["station:8814001", "station:8891009"]
    assert stations[0].category == "station" and stations[0].name == "Brussel-Zuid"


def test_sncb_station_codes_carry_an_s_prefix_their_platforms_do_not():
    # The real feed: stations are location_type 1 with an "S" prefix, foreign places are plain
    # stops with no parent, and a leading "S" must not hide the UIC country code.
    rows = [
        {"stop_id": "S8814001", "stop_name": "Bruxelles-Midi", "stop_lat": "50.835707",
         "stop_lon": "4.336531", "location_type": "1", "parent_station": ""},
        {"stop_id": "8814001", "stop_name": "Bruxelles-Midi platform", "stop_lat": "50.835707",
         "stop_lon": "4.336531", "location_type": "0", "parent_station": "S8814001"},
        {"stop_id": "8001135", "stop_name": "Hamburg-Harburg", "stop_lat": "53.4563", "stop_lon": "9.9918",
         "location_type": "0", "parent_station": ""},
        {"stop_id": "7015400", "stop_name": "London St Pancras (GB)", "stop_lat": "51.530631",
         "stop_lon": "-0.12548", "location_type": "0", "parent_station": ""},
    ]
    assert [s.id for s in read_gtfs_stations(rows)] == ["station:S8814001"]


def test_luxembourg_is_dropped_by_its_uic_code_where_the_bounding_box_cannot():
    # Luxembourg City sits inside any rectangle that also covers Belgium's south-eastern corner,
    # so the country code, not the geometry, is what keeps it out.
    rows = [{"stop_id": "8200100", "stop_name": "Luxembourg", "stop_lat": "49.6002",
             "stop_lon": "6.1342", "location_type": "1", "parent_station": ""},
            {"stop_id": "8866001", "stop_name": "Arlon", "stop_lat": "49.6847", "stop_lon": "5.8107",
             "location_type": "1", "parent_station": ""}]
    assert in_belgium(49.6002, 6.1342)      # the rough box cannot tell these apart
    assert [s.id for s in read_gtfs_stations(rows)] == ["station:8866001"]


def test_belgium_bounds_are_a_rough_guard():
    assert in_belgium(50.8357, 4.3365)      # Brussels
    assert not in_belgium(52.378, 4.900)    # Amsterdam
