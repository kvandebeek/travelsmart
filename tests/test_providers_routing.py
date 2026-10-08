"""Reading per-leg times out of what TomTom and HERE answer."""

import pytest

from travelsmart.providers import here, tomtom


def test_tomtom_legs_carry_live_free_flow_and_typical_times():
    payload = {"routes": [{"legs": [
        {"summary": {"lengthInMeters": 5120, "travelTimeInSeconds": 230,
                     "trafficDelayInSeconds": 35, "noTrafficTravelTimeInSeconds": 195,
                     "historicTrafficTravelTimeInSeconds": 205,
                     "departureTime": "2026-10-09T07:30:00+02:00",
                     "arrivalTime": "2026-10-09T07:33:50+02:00"}},
        {"summary": {"lengthInMeters": 3000, "travelTimeInSeconds": 150,
                     "noTrafficTravelTimeInSeconds": 140}},
    ]}]}
    legs = tomtom.parse_legs(payload)
    assert [leg.index for leg in legs] == [0, 1]
    assert legs[0].metres == 5120 and legs[0].seconds == 230
    assert legs[0].free_flow_seconds == 195 and legs[0].typical_seconds == 205
    assert legs[0].departure.hour == 7 and legs[0].departure.minute == 30
    # A leg may omit the optional times, and must not invent them.
    assert legs[1].typical_seconds is None and legs[1].departure is None


def test_tomtom_handles_a_route_it_could_not_calculate():
    assert tomtom.parse_legs({}) == []
    assert tomtom.parse_legs({"routes": []}) == []


def test_here_sections_carry_base_and_typical_durations():
    payload = {"routes": [{"sections": [
        {"summary": {"length": 5120, "duration": 230, "baseDuration": 195, "typicalDuration": 205}},
        {"summary": {"length": 3000, "duration": 150, "baseDuration": 140}},
    ]}]}
    sections = here.parse_sections(payload)
    assert [s.index for s in sections] == [0, 1]
    assert sections[0].metres == 5120 and sections[0].seconds == 230
    assert sections[0].free_flow_seconds == 195 and sections[0].typical_seconds == 205
    assert sections[1].typical_seconds is None


def test_here_handles_an_empty_answer():
    assert here.parse_sections({}) == []
    assert here.parse_sections({"routes": [{}]}) == []


@pytest.mark.parametrize("module", [tomtom, here])
def test_a_chain_request_needs_at_least_two_waypoints(module):
    with pytest.raises(ValueError):
        module.measure_chain([(51.0, 5.0)], key="unused")
    with pytest.raises(ValueError):
        module.measure_chain([(51.0, 5.0)] * (module.MAX_WAYPOINTS + 1), key="unused")
