"""Chains cover every measured edge once, and a measurement only counts for the road it drove."""

import pytest

from travelsmart.measure.chains import build_chains, chain_waypoints
from travelsmart.measure.validation import accepts, drove_intended_road, length_matches, roads_in_text


def edge(start, end, kind="regional", metres=1000, roads=None):
    return {"from": start, "to": end, "kind": kind, "metres": metres, "roads": roads or []}


def test_a_straight_run_of_edges_becomes_one_chain():
    edges = {"a": edge("n1", "n2"), "b": edge("n2", "n3"), "c": edge("n3", "n4")}
    chains = build_chains(edges)
    assert len(chains) == 1
    assert chains[0]["nodes"] == ["n1", "n2", "n3", "n4"]
    assert chains[0]["edges"] == ["a", "b", "c"]
    assert chains[0]["metres"] == 3000


def test_every_measured_edge_is_covered_by_a_chain():
    # A fork and a join: no single chain can take every edge, but together they must cover each one.
    # A chain may bridge back over an edge another chain already covers; it may not repeat its own.
    edges = {"a": edge("n1", "n2"), "b": edge("n2", "n3"), "c": edge("n2", "n4"),
             "d": edge("n3", "n5"), "e": edge("n4", "n5")}
    chains = build_chains(edges)
    taken = [edge_id for chain in chains for edge_id in chain["edges"]]
    assert set(taken) == {"a", "b", "c", "d", "e"}
    for chain in chains:
        assert len(chain["edges"]) == len(set(chain["edges"]))


def test_a_chain_stops_at_the_leg_limit():
    edges = {f"e{i}": edge(f"n{i}", f"n{i + 1}") for i in range(10)}
    chains = build_chains(edges, max_legs=4)
    assert all(len(chain["edges"]) <= 4 for chain in chains)
    assert all(len(chain["nodes"]) == len(chain["edges"]) + 1 for chain in chains)
    taken = {edge_id for chain in chains for edge_id in chain["edges"]}
    assert len(taken) == 10


def test_a_chain_bridges_over_covered_edges_to_keep_going():
    # Two long stretches joined by a short one. Measuring them as one chain costs one request
    # instead of two, so the chain crosses the middle edge even once it is already covered.
    edges = {"first": edge("n1", "n2", metres=5000), "middle": edge("n2", "n3", metres=250),
             "second": edge("n3", "n4", metres=5000)}
    chains = build_chains(edges)
    assert len(chains) == 1
    assert chains[0]["edges"] == ["first", "middle", "second"]


def test_a_chain_does_not_wander_endlessly_over_covered_edges():
    # One uncovered edge sits far behind a long covered tail; the chain must give up rather than
    # drive the whole tail measuring nothing new.
    edges = {f"tail{i}": edge(f"t{i}", f"t{i + 1}", metres=300) for i in range(20)}
    edges["seed"] = edge("t20", "t21", metres=9000)
    chains = build_chains(edges)
    for chain in chains:
        assert len(chain["edges"]) <= 1 + 20          # never longer than the graph itself
    assert {e for chain in chains for e in chain["edges"]} == set(edges)


def test_access_edges_and_zero_distance_turns_are_not_measured_in_chains():
    edges = {"road": edge("n1", "n2"), "turn": edge("n2", "n3", kind="junction", metres=0),
             "access": edge("n3", "p1", kind="access", metres=800)}
    chains = build_chains(edges)
    assert [chain["edges"] for chain in chains] == [["road"]]


def test_chain_waypoints_follow_the_node_path():
    edges = {"a": edge("n1", "n2"), "b": edge("n2", "n3")}
    nodes = {"n1": {"lat": 51.0, "lon": 5.0}, "n2": {"lat": 51.1, "lon": 5.1},
             "n3": {"lat": 51.2, "lon": 5.2}}
    chain = build_chains(edges)[0]
    assert chain_waypoints(chain, nodes) == [(51.0, 5.0), (51.1, 5.1), (51.2, 5.2)]


def test_chain_waypoints_prefer_the_carriageway_points_of_each_edge():
    # The node sits between the two carriageways; the edge's own ends sit on the road itself, and a
    # request built from the node would let a provider snap to the wrong side and detour.
    edges = {"a": dict(edge("n1", "n2"), start=[51.001, 5.001], end=[51.101, 5.101]),
             "b": dict(edge("n2", "n3"), start=[51.101, 5.101], end=[51.201, 5.201])}
    nodes = {"n1": {"lat": 51.0, "lon": 5.0}, "n2": {"lat": 51.1, "lon": 5.1},
             "n3": {"lat": 51.2, "lon": 5.2}}
    chain = build_chains(edges)[0]
    assert chain_waypoints(chain, nodes, edges) == [(51.001, 5.001), (51.101, 5.101), (51.201, 5.201)]


def test_a_chain_needs_at_least_one_leg():
    with pytest.raises(ValueError):
        build_chains({"a": edge("n1", "n2")}, max_legs=0)


def test_a_leg_counts_only_when_it_is_the_expected_length():
    assert length_matches(1000, 1000)
    assert length_matches(1000, 1140)
    assert length_matches(1000, 860)
    assert not length_matches(1000, 1160)
    assert not length_matches(1000, 500)
    assert not length_matches(0, 0)        # a zero-length edge cannot be checked


def test_road_numbers_are_read_out_of_a_route_description():
    assert roads_in_text("via E40 and N9") == {"E40", "N9"}
    assert roads_in_text("via E 40/A10") == {"E40", "A10"}
    assert roads_in_text("via Chaussée de Charleroi") == set()


def test_a_route_over_another_numbered_road_is_rejected():
    assert drove_intended_road(["E40"], "via E40")
    assert drove_intended_road(["E40", "A10"], "via A10 and some street")
    assert not drove_intended_road(["E40"], "via N9")
    assert drove_intended_road([], "via anything")   # a local stretch no description names


def test_a_route_named_only_by_street_is_not_treated_as_a_detour():
    # Google labels a route the way the signs do, and most Belgian N-roads are signed by street
    # name: the N35 shows up as "via Kouter and Tieltsesteenweg". That is the N35, not a detour.
    assert drove_intended_road(["N35"], "Kouter and Tieltsesteenweg")
    assert drove_intended_road(["N70"], "Grote Baan")
    # Naming a different numbered road still is one.
    assert not drove_intended_road(["N35"], "Kouter and E17")


def test_accepts_explains_why_a_measurement_was_rejected():
    stretch = edge("n1", "n2", metres=1000, roads=["E40"])
    assert accepts(stretch, 1050, text="via E40") == (True, "")
    ok, why = accepts(stretch, 2000, text="via E40")
    assert not ok and "not within 15%" in why
    ok, why = accepts(stretch, 1050, text="via N9")
    assert not ok and "N9" in why
    # TomTom and HERE name no roads, so their legs are judged on length alone.
    assert accepts(stretch, 1050)[0]


def test_google_distances_are_read_in_km_and_in_metres():
    # Short routes, which is most ramps, are written in metres and used to come back as no distance.
    from travelsmart.google_maps_import import distance_km_in
    assert distance_km_in("21.1 km") == 21.1
    assert distance_km_in("1,2 km") == 1.2
    assert distance_km_in("650 m") == 0.65
    assert distance_km_in("24 min") is None          # "min" must not read as metres
    assert distance_km_in("5 min 650 m") == 0.65
    assert distance_km_in(None) is None


def test_an_edge_too_short_to_measure_is_not_targeted():
    # Under 200 m every provider returns a leg several times the expected length, so these only
    # waste the request budget; they are a node-placement question for the builder instead.
    from travelsmart.measure.chains import measurable
    assert measurable(edge("n1", "n2", metres=1000))
    assert not measurable(edge("n1", "n2", metres=150))
    assert not measurable(edge("n1", "n2", kind="access", metres=1000))
    chains = build_chains({"short": edge("n1", "n2", metres=150),
                           "long": edge("n2", "n3", metres=1500)})
    assert [c["edges"] for c in chains] == [["long"]]
