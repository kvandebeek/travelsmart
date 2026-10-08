"""Topology audit catches an unjoined road and an isolated node."""

from travelsmart.network.audit import audit_network


def test_audit_flags_nearby_unjoined_roads():
    nodes = {
        "a": {"kind": "regional", "lat": 51.0, "lon": 5.0, "roads": ["N2"]},
        "b": {"kind": "regional", "lat": 51.0, "lon": 5.001, "roads": ["N2"]},
        "c": {"kind": "regional", "lat": 51.0002, "lon": 5.001, "roads": ["N2"]},
        "d": {"kind": "regional", "lat": 51.0002, "lon": 5.002, "roads": ["N2"]},
        "e": {"kind": "regional", "lat": 51.1, "lon": 5.1, "roads": ["N3"]},
    }
    edges = {
        "first": {"from": "a", "to": "b", "kind": "regional", "roads": ["N2"],
                  "path": [[51.0, 5.0], [51.0, 5.001]]},
        "second": {"from": "c", "to": "d", "kind": "regional", "roads": ["N2"],
                   "path": [[51.0002, 5.001], [51.0002, 5.002]]},
    }
    report = audit_network(nodes, edges)
    assert report["summary"]["components"] == 3
    assert report["summary"]["isolated_nodes"] == 1
    assert any(issue["node"] == "c" and issue["category"] == "possible_gap" and
               issue["nearest"]["other_component"] for issue in report["issues"])
    assert any(issue["node"] == "e" and issue["category"] == "isolated_node"
               for issue in report["issues"])


def test_audit_flags_a_shared_osm_junction_without_a_graph_link():
    nodes = {
        "r1": {"kind": "regional", "lat": 51.0, "lon": 5.0, "roads": ["N2"], "osm_nodes": [1]},
        "r2": {"kind": "regional", "lat": 51.0, "lon": 5.001, "roads": ["N2"], "osm_nodes": [2]},
    }
    edges = {"road": {"from": "r1", "to": "r2", "kind": "regional", "roads": ["N2"],
                      "path": [[51.0, 5.0], [51.0, 5.001]]}}
    source = {
        10: {"nodes": [1, 2], "tags": {"highway": "primary", "ref": "N2"}},
        11: {"nodes": [2, 3], "tags": {"highway": "motorway_link"}},
        12: {"nodes": [4, 5], "tags": {"highway": "primary", "ref": "N2"}},
        13: {"nodes": [4, 6], "tags": {"highway": "motorway_link"}},
    }
    report = audit_network(nodes, edges, source, {4: (51.1, 5.1)})
    assert report["summary"]["source_junctions_missing_link"] == 1
    assert report["summary"]["source_junctions_unrepresented_far"] == 1
    assert any(issue["node"] == "r2" and issue["category"] == "source_junction_missing_link"
               for issue in report["issues"])


def test_audit_accepts_separate_motorway_and_connection_nodes_at_one_osm_junction():
    nodes = {
        "m2": {"kind": "motorway", "lat": 51.0, "lon": 5.0, "roads": ["R0"],
               "osm_nodes": [2]},
        "c2": {"kind": "connection", "lat": 51.0, "lon": 5.0, "roads": ["N3"],
               "osm_nodes": [2]},
        "r3": {"kind": "regional", "lat": 51.0, "lon": 5.001, "roads": ["N3"],
               "osm_nodes": [3]},
    }
    edges = {
        "turn": {"from": "m2", "to": "c2", "kind": "junction", "roads": ["R0", "N3"],
                 "path": [[51.0, 5.0], [51.0, 5.0]]},
        "road": {"from": "c2", "to": "r3", "kind": "regional", "roads": ["N3"],
                 "path": [[51.0, 5.0], [51.0, 5.001]]},
    }
    source = {
        10: {"nodes": [2, 4], "tags": {"highway": "motorway", "ref": "R0"}},
        11: {"nodes": [2, 3], "tags": {"highway": "primary", "ref": "N3"}},
    }
    report = audit_network(nodes, edges, source)
    assert report["summary"]["source_junctions_missing_link"] == 0
    assert report["summary"]["source_junctions_unrepresented"] == 0


def test_audit_checks_each_exit_direction_against_the_backbone():
    nodes = {
        "r1": {"kind": "regional", "lat": 51.0, "lon": 5.0, "roads": ["N2"]},
        "r2": {"kind": "regional", "lat": 51.0, "lon": 5.001, "roads": ["N2"]},
        "c": {"kind": "connection", "lat": 51.001, "lon": 5.0, "roads": []},
        "m": {"kind": "motorway", "lat": 51.002, "lon": 5.0, "roads": ["E40"]},
        "remote": {"kind": "connection", "lat": 51.1, "lon": 5.1, "roads": []},
    }
    def edge(start, end, kind, roads=None):
        return {"from": start, "to": end, "kind": kind, "roads": roads or [],
                "path": [[nodes[start]["lat"], nodes[start]["lon"]],
                         [nodes[end]["lat"], nodes[end]["lon"]]]}
    edges = {
        "backbone": edge("r1", "r2", "regional", ["N2"]),
        "local": edge("c", "r1", "regional"),
        "off": edge("m", "c", "ramp_off"),
        "on": edge("c", "m", "ramp_on"),
        "remote_off": edge("m", "remote", "ramp_off"),
    }
    report = audit_network(nodes, edges)
    assert report["summary"]["exit_connections_checked"] == 2
    assert report["summary"]["exit_connections_without_backbone_path"] == 1
    assert report["summary"]["exit_to_backbone_direction_gaps"] == 0
    assert report["summary"]["backbone_to_entry_direction_gaps"] == 1
    assert any(issue["category"] == "directional_backbone_gap" and
               issue["missing_directions"] == ["backbone_to_entry"] for issue in report["issues"])
    assert any(issue["category"] == "no_backbone_path" and issue["node"] == "remote"
               for issue in report["issues"])


def test_audit_recognises_non_n_r_backbone_refs():
    # Brussels (B201), regional/motorway-extension (A112) and European-route (E314) refs anchor the
    # backbone too, matching osm.py's wanted(); otherwise an exit reaching only such a road would be
    # wrongly flagged as having no path to the backbone at all.
    nodes = {
        "r1": {"kind": "regional", "lat": 51.0, "lon": 5.0, "roads": ["B201"]},
        "r2": {"kind": "regional", "lat": 51.0, "lon": 5.001, "roads": ["B201"]},
        "c": {"kind": "connection", "lat": 51.001, "lon": 5.0, "roads": []},
        "m": {"kind": "motorway", "lat": 51.002, "lon": 5.0, "roads": ["E40"]},
    }
    def edge(start, end, kind, roads=None):
        return {"from": start, "to": end, "kind": kind, "roads": roads or [],
                "path": [[nodes[start]["lat"], nodes[start]["lon"]],
                         [nodes[end]["lat"], nodes[end]["lon"]]]}
    edges = {
        "backbone": edge("r1", "r2", "regional", ["B201"]),
        "local": edge("c", "r1", "regional"),
        "off": edge("m", "c", "ramp_off"),
        "on": edge("c", "m", "ramp_on"),
    }
    report = audit_network(nodes, edges)
    assert report["summary"]["exit_connections_without_backbone_path"] == 0
    assert not any(issue["category"] == "no_backbone_path" for issue in report["issues"])


def test_audit_excludes_a_service_area_from_public_exit_path_checks():
    nodes = {
        "c": {"kind": "connection", "lat": 51.0, "lon": 5.0, "roads": [],
              "access_classes": ["motorway_link", "service"]},
        "m": {"kind": "motorway", "lat": 51.001, "lon": 5.0, "roads": ["E40"]},
    }
    edges = {"off": {"from": "m", "to": "c", "kind": "ramp_off", "roads": [],
                     "path": [[51.001, 5.0], [51.0, 5.0]]}}
    report = audit_network(nodes, edges)
    assert report["summary"]["exit_connections_checked"] == 0
    assert not any(issue["category"] == "no_backbone_path" for issue in report["issues"])
