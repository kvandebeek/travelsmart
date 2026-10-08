"""Export a compact graph the browser can route on (docs/network-design.md §9).

The search runs in the browser, so what it loads has to be small. network.json is 18 MB, almost all
of it the OpenStreetMap geometry of each edge, which routing never looks at: the search needs only
which node leads to which, how long the stretch is and how it behaves over the week. Dropping the
geometry leaves well under a megabyte.

Arrays rather than objects, because repeating the same dozen key names across fourteen thousand
edges is most of what a JSON file of objects weighs.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from travelsmart.measure.routing import FREE_FLOW_KMH

# Fixed order, so the browser can read a kind back from its number.
KINDS = ["motorway", "regional", "transfer", "ramp_on", "ramp_off", "ramp_link", "junction", "access"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", type=Path, default=Path("data/network/network.json"))
    parser.add_argument("--profiles", type=Path, default=Path("data/network/profiles.json"))
    parser.add_argument("--endpoints", type=Path, default=Path("data/network/endpoints.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("public/data"))
    args = parser.parse_args()

    network = json.loads(args.network.read_text(encoding="utf-8"))
    nodes, edges = network["nodes"], network["edges"]

    # Endpoint clusters are nodes too, reached over their access edges, so a trip can start at a
    # station rather than at whatever junction happens to be nearby (§10.1).
    clusters, access, places = {}, {}, []
    if args.endpoints.exists():
        endpoints = json.loads(args.endpoints.read_text(encoding="utf-8"))
        clusters = endpoints["clusters"]
        access = endpoints["edges"]
        named = {}
        for endpoint_id, endpoint in endpoints["endpoints"].items():
            name = (endpoint.get("name") or "").strip()
            if name and endpoint["cluster"] in clusters:
                named.setdefault((name, endpoint["category"]), endpoint["cluster"])
        places = [[name, category, cluster] for (name, category), cluster in sorted(named.items())]

    node_ids = sorted(nodes) + sorted(clusters)
    index_of = {node_id: index for index, node_id in enumerate(node_ids)}
    kind_of = {kind: number for number, kind in enumerate(KINDS)}

    exported_edges = []
    edge_ids = []
    for edge_id, edge in sorted({**edges, **access}.items()):
        if edge["from"] not in index_of or edge["to"] not in index_of:
            continue
        edge_ids.append(edge_id)
        exported_edges.append([index_of[edge["from"]], index_of[edge["to"]],
                               round(edge["metres"]), kind_of.get(edge["kind"], len(KINDS)),
                               edge.get("roads", [])])

    graph = {
        "kinds": KINDS,
        "freeFlowKmh": [FREE_FLOW_KMH.get(kind, 50) for kind in KINDS],
        "nodeIds": node_ids,
        "nodes": [[round((nodes.get(node_id) or clusters[node_id])["lat"], 5),
                   round((nodes.get(node_id) or clusters[node_id])["lon"], 5)]
                  for node_id in node_ids],
        "junctionCount": len(nodes),
        "places": places,
        "edgeIds": edge_ids,
        "edges": exported_edges,
    }

    profiles = {}
    if args.profiles.exists():
        published = json.loads(args.profiles.read_text(encoding="utf-8"))["profiles"]
        for edge_id, entry in published.items():
            if edge_id not in index_of and edge_id not in set(edge_ids):
                continue
            # Only what the search reads: the usual time and each cell's median.
            profiles[edge_id] = {"usual": entry.get("usual", 0),
                                 "cells": {key: cell["median"]
                                           for key, cell in entry.get("cells", {}).items()}}

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in (("graph.json", graph), ("profiles.json", profiles)):
        target = args.output_dir / name
        temp = target.with_suffix(".partial")
        temp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        temp.replace(target)
        print(f"{target}: {target.stat().st_size / 1_000_000:.2f} MB", file=sys.stderr)
    print(f"{len(nodes):,} junctions and {len(clusters):,} endpoint clusters, "
          f"{len(exported_edges):,} edges, {len(places):,} named places, "
          f"{len(profiles):,} measured edges", file=sys.stderr)


if __name__ == "__main__":
    main()
