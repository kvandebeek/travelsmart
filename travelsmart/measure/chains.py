"""Cut the network into chains, the unit one provider request measures (docs/network-design.md §6.1).

A chain is a path along neighbouring nodes: the route A -> B -> C -> ... that a provider is asked for
in one call. It returns a summary per leg, so one request measures every edge of the chain, each at
the moment it is actually driven.

Every measured edge is in at least one chain. A chain prefers edges no chain has taken yet, but may
bridge through a few already-covered ones to keep going: the request measures every leg either way,
so a bridge costs nothing and turns many two-leg chains into one long one. Fewer, longer chains mean
fewer requests for the same coverage, which is the whole budget question in §6.1.
"""

from __future__ import annotations

from collections import defaultdict

MAX_LEGS = 150          # TomTom takes up to 150 waypoints in one Calculate Route request
# How far a chain may run over already-covered edges to reach uncovered ones. Bridging is what makes
# chains long, but a chain that bridges on and on is just driving around measuring nothing new.
# Over the Belgian network this is the knee of the curve: 0 gives 2,838 chains of a median 2 legs,
# 12 gives 1,666 of a median 19, and allowing more barely saves another 5% of requests while the
# legs measured twice keep climbing (2.6x the edge count here, 4x at 50).
MAX_BRIDGE_LEGS = 12
# Zero-distance turns at a shared junction have nothing to measure, and an access edge belongs to an
# endpoint cluster rather than the backbone; §6.4 measures those on their own rhythm.
MEASURED_KINDS = frozenset({"motorway", "regional", "transfer", "ramp_on", "ramp_off", "ramp_link"})
# Below this, an edge cannot be measured reliably by anyone. Measured on 9 October 2026: edges under
# 200 m failed §4's length check 77% of the time with TomTom, 80% with HERE and 43% with Google, and
# the failures are not near misses but legs several times the expected length. Two waypoints that
# close together snap ambiguously, or the provider has to drive on and come back to reach the second
# one legally. Chaining does not help: it is worse than Google's single search. These stretches are
# a node-placement question for the builder (§4), and until then they only waste the request budget,
# so nothing targets them. A route crosses them on free flow, which for 150 m changes little.
MIN_MEASURABLE_METRES = 200


def measurable(edge: dict, kinds: frozenset[str] = MEASURED_KINDS,
               min_metres: float = MIN_MEASURABLE_METRES) -> bool:
    """Whether this edge is worth asking a provider about at all."""
    return edge["kind"] in kinds and edge["metres"] >= min_metres


def build_chains(edges: dict[str, dict], max_legs: int = MAX_LEGS,
                 kinds: frozenset[str] = MEASURED_KINDS,
                 max_bridge: int = MAX_BRIDGE_LEGS,
                 min_metres: float = MIN_MEASURABLE_METRES) -> list[dict]:
    """Group the measured edges into chains of at most max_legs consecutive legs.

    Returns one dict per chain with its node path, its edge ids in order, and the total distance.
    """
    if max_legs < 1:
        raise ValueError("a chain needs at least one leg")
    # Anything of a measured kind may be *driven* through, including stretches too short to judge:
    # they are the connectors that keep a chain long, and dropping them splintered the chains into
    # half the length and a third more requests. What they are not is a reason to send a request, so
    # coverage is tracked over the measurable ones alone.
    measured = {edge_id: edge for edge_id, edge in edges.items() if edge["kind"] in kinds}
    needed = {edge_id for edge_id, edge in measured.items() if edge["metres"] >= min_metres}
    out_edges: dict[str, list[str]] = defaultdict(list)
    in_edges: dict[str, list[str]] = defaultdict(list)
    for edge_id, edge in measured.items():
        out_edges[edge["from"]].append(edge_id)
        in_edges[edge["to"]].append(edge_id)

    unused = set(needed)
    chains = []
    # Longest first, so a chain starts on a stretch that is expensive to measure on its own.
    for seed in sorted(needed, key=lambda edge_id: -measured[edge_id]["metres"]):
        if seed not in unused:
            continue
        unused.discard(seed)
        path = [seed]
        taken = {seed}
        # Grow forward, then backward, while the chain has legs to spare.
        for append, side in ((path.append, "to"), (lambda e: path.insert(0, e), "from")):
            bridged = 0
            while len(path) < max_legs:
                end = measured[path[-1]]["to"] if side == "to" else measured[path[0]]["from"]
                following = _pick(out_edges[end] if side == "to" else in_edges[end],
                                  unused, measured, taken)
                if following is None:
                    break
                bridged = 0 if following in unused else bridged + 1
                if bridged > max_bridge:
                    break
                unused.discard(following)
                taken.add(following)
                append(following)
        nodes = [measured[path[0]]["from"]] + [measured[edge_id]["to"] for edge_id in path]
        chains.append({"id": f"c{len(chains):05d}", "nodes": nodes, "edges": path,
                       "metres": sum(measured[edge_id]["metres"] for edge_id in path)})
    return chains


def _pick(candidates: list[str], unused: set[str], measured: dict[str, dict],
          taken: set[str]) -> str | None:
    """The next leg: an uncovered edge if there is one, else one to bridge through.

    The longest is preferred either way, so a chain follows the through road rather than turning off
    at every junction. An edge already in this chain is never taken again, so the walk cannot loop.
    """
    fresh = [edge_id for edge_id in candidates if edge_id in unused]
    if fresh:
        return max(fresh, key=lambda edge_id: measured[edge_id]["metres"])
    bridges = [edge_id for edge_id in candidates if edge_id not in taken]
    return max(bridges, key=lambda edge_id: measured[edge_id]["metres"], default=None)


def chain_waypoints(chain: dict, nodes: dict[str, dict],
                    edges: dict[str, dict] | None = None) -> list[tuple[float, float]]:
    """The waypoints a provider request asks for, as (lat, lon) in driving order.

    These come from the edges, not the nodes. A node is a *cluster* of OSM junctions and its lat/lon
    is their average, which can sit between carriageways or beside the road; a provider snapping to
    that can pick the wrong carriageway and detour kilometres. Each edge carries its own start and
    end on the carriageway it uses (§4), so asking from one edge's start to its end drives exactly
    that stretch, in that direction.
    """
    if not edges:
        return [(nodes[node_id]["lat"], nodes[node_id]["lon"]) for node_id in chain["nodes"]]
    first = edges[chain["edges"][0]]
    points = [tuple(first["start"])]
    points += [tuple(edges[edge_id]["end"]) for edge_id in chain["edges"]]
    return points
