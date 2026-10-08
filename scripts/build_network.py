"""Build the Belgian road graph and its local review data from a Geofabrik extract."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from travelsmart.network.build import MAX_EDGE_METRES, build_network
from travelsmart.network.connectors import select_network_ways
from travelsmart.network.osm import EXTRACT, EXTRACT_URL, load_belgium


class Progress:
    """A compact, monotonic progress display that also works in redirected logs."""

    def __init__(self) -> None:
        self.started = time.monotonic()
        self.percent = -1
        self.message = ""
        self.interactive = sys.stderr.isatty()

    def update(self, percent: float, message: str) -> None:
        percent = min(100, max(self.percent, round(percent)))
        if percent == self.percent and message == self.message:
            return
        self.percent, self.message = percent, message
        elapsed = time.monotonic() - self.started
        line = f"{percent:3d}%  {message}  ({elapsed:,.0f}s)"
        if self.interactive:
            width = 30
            filled = round(width * percent / 100)
            line = f"{percent:3d}% [{'#' * filled}{'.' * (width - filled)}] {message}  ({elapsed:,.0f}s)"
            print(f"\r\033[2K{line[:max(1, 160)]}", end="", file=sys.stderr, flush=True)
        else:
            print(line, file=sys.stderr, flush=True)

    def finish(self, message: str = "complete") -> None:
        self.update(100, message)
        if self.interactive:
            print(file=sys.stderr)

    def log(self, message: str) -> None:
        """Write a normal status line without colliding with the live progress row."""
        if self.interactive:
            print("\r\033[2K", end="", file=sys.stderr, flush=True)
        print(message, file=sys.stderr, flush=True)


def download(progress: Progress) -> None:
    EXTRACT.parent.mkdir(parents=True, exist_ok=True)
    partial = EXTRACT.with_suffix(".partial")
    request = urllib.request.Request(EXTRACT_URL, headers={"User-Agent": "TravelSmart network builder"})
    try:
        with urllib.request.urlopen(request, timeout=180) as response, partial.open("wb") as target:
            total = int(response.headers.get("Content-Length", 0))
            downloaded = 0
            while chunk := response.read(1024 * 1024):
                target.write(chunk)
                downloaded += len(chunk)
                if total:
                    progress.update(20 * downloaded / total,
                                    f"downloading Belgium extract ({downloaded / 1_000_000:.0f}/{total / 1_000_000:.0f} MB)")
        partial.replace(EXTRACT)
    finally:
        partial.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="refresh the local Geofabrik extract first")
    parser.add_argument("--extract", type=Path, default=EXTRACT, help="path to a local .osm.pbf extract")
    parser.add_argument("--output", type=Path, default=Path("data/network/network.json"))
    args = parser.parse_args()
    progress = Progress()
    if args.download:
        progress.update(0, "starting download")
        download(progress)
    else:
        progress.update(0, "checking source extract")

    def source_progress(message: str) -> None:
        milestones = {"OSM scan 0%": 20, "OSM scan 25%": 28, "OSM scan 50%": 36,
                      "OSM scan 75%": 46, "OSM scan 100%": 55, "Using cached": 55}
        percent = next((value for prefix, value in milestones.items() if message.startswith(prefix)), 52)
        progress.update(percent, message)

    data = load_belgium(args.extract, progress=source_progress)
    progress.update(55, f"selecting connectors from {len(data.ways):,} candidate roads")
    data = select_network_ways(data, progress=source_progress,
                               update=lambda fraction, message: progress.update(55 + 15 * fraction, message))
    progress.update(70, f"building graph from {len(data.ways):,} selected roads")
    network = build_network(data, update=lambda fraction, message: progress.update(70 + 20 * fraction, message))
    progress.update(90, "validating edge lengths and endpoints")
    too_long = [(key, edge["metres"]) for key, edge in network.edges.items()
                if edge["metres"] > MAX_EDGE_METRES]
    if too_long:
        raise RuntimeError(f"{len(too_long)} edges exceed the length cap; first: {too_long[:5]}")
    dangling = [(key, edge) for key, edge in network.edges.items()
                if edge["from"] not in network.nodes or edge["to"] not in network.nodes]
    if dangling:
        raise RuntimeError(f"{len(dangling)} edges refer to missing nodes")
    progress.update(93, "writing network data")
    output = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(".partial")
    with temp.open("w", encoding="utf-8") as file:
        json.dump({"source": str(args.extract), "source_way_ids": sorted(data.ways),
                   "nodes": network.nodes, "edges": network.edges},
                  file, separators=(",", ":"))
    temp.replace(output)
    progress.update(97, "writing network audit")
    progress.log(f"Saved {len(network.nodes):,} nodes and {len(network.edges):,} directed edges to {output}")
    progress.log(f"Nodes: {dict(Counter(node['kind'] for node in network.nodes.values()))}")
    progress.log(f"Edges: {dict(Counter(edge['kind'] for edge in network.edges.values()))}")
    from scripts.audit_network import write_audit
    write_audit(output, data.ways, data.points,
                update=lambda fraction, message: progress.update(97 + 3 * fraction, message), log=progress.log)
    progress.finish("network and audit saved")


if __name__ == "__main__":
    main()
