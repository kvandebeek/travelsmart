"""Does a measurement belong to the edge it was asked for? (docs/network-design.md §4)

A provider is free to route around a jam. When it does, the time it returns is for another road and
would poison the edge's profile, so it is rejected rather than stored. The rules differ per provider
only in what each one tells us: TomTom and HERE return a leg length and no road names, while Google
names the roads it used.

Edges that keep failing validation have badly placed nodes. That is fixed in the network, not here.
"""

from __future__ import annotations

import re

LENGTH_TOLERANCE = 0.15     # §4: within +/-15% of the expected length
# A road number as it appears in a route description: "E40", "E 40", "A2", "N 70", "R0".
ROAD_IN_TEXT = re.compile(r"\b([AENRB])\s?(\d{1,4})\b", re.IGNORECASE)


def length_matches(expected_metres: float, measured_metres: float,
                   tolerance: float = LENGTH_TOLERANCE) -> bool:
    """Whether a measured leg is the expected length. An edge of zero length cannot be checked."""
    if expected_metres <= 0:
        return False
    return abs(measured_metres - expected_metres) <= tolerance * expected_metres


def roads_in_text(text: str) -> set[str]:
    """The road numbers a route description mentions, normalised ("E 40" and "e40" -> "E40")."""
    return {f"{letter.upper()}{number}" for letter, number in ROAD_IN_TEXT.findall(text or "")}


def drove_intended_road(intended: list[str], text: str) -> bool:
    """Whether a described route used one of the edge's intended road numbers.

    An edge with no intended number is a local stretch between junctions, which no description names;
    those are judged on length alone.
    """
    if not intended:
        return True
    return bool(set(intended) & roads_in_text(text))


def accepts(edge: dict, measured_metres: float, *, text: str | None = None,
            tolerance: float = LENGTH_TOLERANCE) -> tuple[bool, str]:
    """Whether this measurement counts for this edge, and why not when it does not.

    `text` is a route description where the provider gives one (Google's "via"); TomTom and HERE
    return no road names, so their legs are judged on length alone.
    """
    if not length_matches(edge["metres"], measured_metres, tolerance):
        return False, (f"length {measured_metres:.0f} m is not within "
                       f"{tolerance:.0%} of the expected {edge['metres']:.0f} m")
    if text is not None and not drove_intended_road(edge.get("roads", []), text):
        return False, f"route over {sorted(roads_in_text(text)) or 'unnamed roads'}, not {edge['roads']}"
    return True, ""
