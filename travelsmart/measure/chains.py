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


def build_chains(edges: dict[str, dict], max_legs: int = MAX_LEGS,
                 kinds: frozenset[str] = MEASURED_KINDS,
                 max_bridge: int = MAX_BRIDGE_LEGS) -> list[dict]:
    """Group the measured edges into chains of at most max_legs consecutive legs.

    Returns one dict per chain with its node path, its edge ids in order, and the total distance.
    """
    if max_legs < 1:
        raise ValueError("a chain needs at least one leg")
    measured = {edge_id: edge for edge_id, edge in edges.items() if edge["kind"] in kinds}
    out_edges: dict[str, list[str]] = defaultdict(list)
    in_edges: dict[str, list[str]] = defaultdict(list)
    for edge_id, edge in measured.items():
        out_edges[edge["from"]].append(edge_id)
        in_edges[edge["to"]].append(edge_id)

    unused = set(measured)
    chains = []
    # Longest first, so a chain starts on a stretch that is expensive to measure on its own.
    for seed in sorted(measured, key=lambda edge_id: -measured[edge_id]["metres"]):
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


def chain_waypoints(chain: dict, nodes: dict[str, dict]) -> list[tuple[float, float]]:
    """The chain's nodes as (lat, lon), the waypoints a provider request asks for."""
    return [(nodes[node_id]["lat"], nodes[node_id]["lon"]) for node_id in chain["nodes"]]
