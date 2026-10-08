"""Build the Belgian road graph and its local review data from a Geofabrik extract."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from travelsmart.network.build import MAX_EDGE_METRES, build_network
from travelsmart.network.connectors import select_network_ways
from travelsmart.network.osm import EXTRACT, EXTRACT_URL, load_belgium


def download() -> None:
    EXTRACT.parent.mkdir(parents=True, exist_ok=True)
    partial = EXTRACT.with_suffix(".partial")
    request = urllib.request.Request(EXTRACT_URL, headers={"User-Agent": "TravelSmart network builder"})
    try:
        with urllib.request.urlopen(request, timeout=180) as response, partial.open("wb") as target:
            while chunk := response.read(1024 * 1024):
                target.write(chunk)
        partial.replace(EXTRACT)
    finally:
        partial.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="refresh the local Geofabrik extract first")
    parser.add_argument("--extract", type=Path, default=EXTRACT, help="path to a local .osm.pbf extract")
    parser.add_argument("--output", type=Path, default=Path("data/network/network.json"))
    args = parser.parse_args()
    if args.download:
        download()
    data = select_network_ways(load_belgium(args.extract))
    network = build_network(data)
    too_long = [(key, edge["metres"]) for key, edge in network.edges.items()
                if edge["metres"] > MAX_EDGE_METRES]
    if too_long:
        raise RuntimeError(f"{len(too_long)} edges exceed the length cap; first: {too_long[:5]}")
    dangling = [(key, edge) for key, edge in network.edges.items()
                if edge["from"] not in network.nodes or edge["to"] not in network.nodes]
    if dangling:
        raise RuntimeError(f"{len(dangling)} edges refer to missing nodes")
    output = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(".partial")
    with temp.open("w", encoding="utf-8") as file:
        json.dump({"source": str(args.extract), "source_way_ids": sorted(data.ways),
                   "nodes": network.nodes, "edges": network.edges},
                  file, separators=(",", ":"))
    temp.replace(output)
    print(f"Saved {len(network.nodes):,} nodes and {len(network.edges):,} directed edges to {output}")
    print("Nodes:", dict(Counter(node["kind"] for node in network.nodes.values())))
    print("Edges:", dict(Counter(edge["kind"] for edge in network.edges.values())))
    from scripts.audit_network import write_audit
    write_audit(output, data.ways, data.points)


if __name__ == "__main__":
    main()
