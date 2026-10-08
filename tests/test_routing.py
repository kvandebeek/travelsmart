"""Time-dependent routing: an edge costs what it costs when the trip gets there (§9)."""

from travelsmart.measure.profiles import build_profile
from travelsmart.measure.routing import (departure_sweep, edge_seconds, fastest_route,
                                         free_flow_seconds, minute_of_week)


def edge(start, end, kind="regional", metres=1000):
    return {"from": start, "to": end, "kind": kind, "metres": metres, "roads": []}


def flat_profile(edge_id, seconds):
    return build_profile(edge_id, "tomtom",
                         [(weekday, half, seconds) for weekday in range(7) for half in range(48)])


def test_an_unmeasured_edge_falls_back_on_free_flow():
    motorway = edge("a", "b", kind="motorway", metres=11_000)
    seconds, measured = edge_seconds(motorway, None, minute_of_week(0, 8 * 60))
    assert not measured
    assert abs(seconds - free_flow_seconds(motorway)) < 0.01
    assert 340 < seconds < 380              # 11 km at 110 km/h


def test_a_measured_edge_uses_its_cell():
    profile = flat_profile("e1", 240)
    seconds, measured = edge_seconds(edge("a", "b"), profile, minute_of_week(2, 9 * 60))
    assert measured and seconds == 240


def test_time_is_interpolated_between_the_two_nearest_half_hours():
    # 08:00 takes 200 s, 08:30 takes 400 s. A trip arriving at 08:15 is halfway between the two
    # cell midpoints, so it must not jump from one to the other at the boundary.
    samples = [(0, 16, 200)] * 3 + [(0, 17, 400)] * 3
    profile = build_profile("e1", "tomtom", samples)
    at_eight = edge_seconds(edge("a", "b"), profile, minute_of_week(0, 8 * 60))[0]
    at_quarter = edge_seconds(edge("a", "b"), profile, minute_of_week(0, 8 * 60 + 15))[0]
    at_half = edge_seconds(edge("a", "b"), profile, minute_of_week(0, 8 * 60 + 30))[0]
    assert at_eight == 200 and at_half == 400
    assert 290 < at_quarter < 310


def test_the_quickest_way_is_taken():
    edges = {"slow": edge("a", "b", metres=9000), "quick": edge("a", "b", metres=1000),
             "last": edge("b", "c", metres=1000)}
    route = fastest_route(edges, {}, "a", "c", minute_of_week(0, 8 * 60))
    assert route.edges == ["quick", "last"]
    assert route.nodes == ["a", "b", "c"]


def test_an_edge_is_costed_for_the_moment_the_trip_reaches_it():
    # The first edge takes an hour. The second is quick at 08:00 and slow at 09:00, and the trip
    # arrives at 09:00, so the slow time is the one that counts.
    edges = {"first": edge("a", "b"), "second": edge("b", "c")}
    first = flat_profile("first", 3600)
    second = build_profile("second", "tomtom",
                           [(0, half, 60 if half < 18 else 600) for half in range(48) for _ in range(3)])
    route = fastest_route(edges, {"first": first, "second": second}, "a", "c",
                          minute_of_week(0, 8 * 60))
    assert route.seconds == 3600 + 600      # not 3600 + 60
    assert route.measured_edges == 2 and route.measured_share == 1.0


def test_a_route_says_how_much_of_it_rests_on_measurements():
    edges = {"first": edge("a", "b"), "second": edge("b", "c")}
    route = fastest_route(edges, {"first": flat_profile("first", 100)}, "a", "c",
                          minute_of_week(0, 8 * 60))
    assert route.measured_edges == 1 and route.measured_share == 0.5


def test_an_unreachable_destination_gives_nothing():
    edges = {"only": edge("a", "b")}
    assert fastest_route(edges, {}, "a", "z", minute_of_week(0, 0)) is None


def test_going_nowhere_takes_no_time():
    route = fastest_route({"only": edge("a", "b")}, {}, "a", "a", minute_of_week(0, 0))
    assert route.seconds == 0 and route.edges == []


def test_a_sweep_covers_every_half_hour_of_the_day():
    edges = {"only": edge("a", "b")}
    sweep = departure_sweep(edges, {}, "a", "b", weekday=1)
    assert len(sweep) == 48
    assert [half for half, _ in sweep] == list(range(48))
    assert all(seconds is not None for _, seconds in sweep)


def test_the_week_wraps_around_midnight_on_sunday():
    assert minute_of_week(0, 0) == 0
    assert minute_of_week(6, 23 * 60 + 59) == 7 * 24 * 60 - 1
    profile = flat_profile("e1", 120)
    # Late on Sunday the next half hour is Monday's; it must roll round rather than fall off.
    seconds, measured = edge_seconds(edge("a", "b"), profile, minute_of_week(6, 23 * 60 + 45))
    assert measured and seconds == 120
