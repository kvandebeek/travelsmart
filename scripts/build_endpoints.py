"""Build the endpoint clusters and access edges that hang trips off the road network (§3.3, §4)."""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import zipfile
from collections import Counter
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.build_network import Progress
from travelsmart.config import ROOT
from travelsmart.network.endpoints import (Endpoint, build_endpoints, load_osm_endpoints,
                                           read_gtfs_stations)
from travelsmart.network.osm import EXTRACT, load_belgium
from travelsmart.verified_fetch import USER_AGENT, verified_context

SNCB_GTFS_URL = "https://data.gtfs.be/sncb/gtfs/be-sncb-gtfs.zip"
SNCB_GTFS = ROOT / "data" / "gtfs" / "be-sncb-gtfs.zip"


def download(url: str, target: Path, progress: Progress) -> None:
    """Fetch the feed over a fully verified connection (see travelsmart.verified_fetch)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".partial")
    try:
        with httpx.Client(verify=verified_context(), timeout=180, follow_redirects=True,
                          headers={"User-Agent": USER_AGENT}) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                total = int(response.headers.get("Content-Length", 0))
                with partial.open("wb") as file:
                    for chunk in response.iter_bytes(1024 * 1024):
                        file.write(chunk)
                        progress.update(10 * response.num_bytes_downloaded / total if total else 5,
                                        f"downloading {target.name} "
                                        f"({response.num_bytes_downloaded / 1_000_000:.1f} MB)")
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)


def read_stations(archive: Path) -> list[Endpoint]:
    with zipfile.ZipFile(archive) as zipped:
        with zipped.open("translations.txt") as source:
            translations = {
                row["field_value"].strip(): row["translation"].strip()
                for row in csv.DictReader(io.TextIOWrapper(source, encoding="utf-8-sig"))
                if row.get("table_name") == "stops"
                and row.get("field_name") == "stop_name"
                and row.get("language") == "nl"
                and (row.get("field_value") or "").strip()
                and (row.get("translation") or "").strip()
            }
        with zipped.open("stops.txt") as stops:
            return read_gtfs_stations(csv.DictReader(io.TextIOWrapper(stops, encoding="utf-8-sig")),
                                      translations)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="refresh the local GTFS feed first")
    parser.add_argument("--extract", type=Path, default=EXTRACT, help="path to a local .osm.pbf extract")
    parser.add_argument("--network", type=Path, default=Path("data/network/network.json"))
    parser.add_argument("--output", type=Path, default=Path("data/network/endpoints.json"))
    args = parser.parse_args()
    progress = Progress()
    if args.download or not SNCB_GTFS.exists():
        progress.update(0, "starting download")
        download(SNCB_GTFS_URL, SNCB_GTFS, progress)

    progress.update(10, "reading stations")
    endpoints = read_stations(SNCB_GTFS)
    progress.log(f"{len(endpoints):,} Belgian stations read from {SNCB_GTFS.name}")

    progress.update(12, "reading the network")
    network = json.loads(args.network.read_text(encoding="utf-8"))

    def source_progress(message: str) -> None:
        progress.update(20 if message.startswith("Using cached") else 15, message)

    endpoints += load_osm_endpoints(args.extract, progress=source_progress)
    progress.log(f"{len(endpoints):,} endpoints in total: "
                 f"{dict(Counter(e.category for e in endpoints))}")

    # Deliberately the whole candidate set, not select_network_ways' backbone: an endpoint sits on an
    # ordinary street, and the cache already holds every primary/secondary/tertiary road in Belgium.
    data = load_belgium(args.extract, progress=source_progress)
    progress.update(40, f"linking {len(endpoints):,} endpoints to {len(network['nodes']):,} network nodes "
                        f"over {len(data.ways):,} candidate roads")
    result = build_endpoints(endpoints, network["nodes"], data,
                             update=lambda fraction, message: progress.update(40 + 55 * fraction, message))

    missing = [edge for edge in result.edges.values()
               if edge["from"] not in result.clusters and edge["from"] not in network["nodes"]
               or edge["to"] not in result.clusters and edge["to"] not in network["nodes"]]
    if missing:
        raise RuntimeError(f"{len(missing)} access edges refer to unknown nodes")

    progress.update(96, "writing endpoint data")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(".partial")
    with temp.open("w", encoding="utf-8") as file:
        json.dump({"source": {"stations": SNCB_GTFS_URL, "network": str(args.network)},
                   "endpoints": result.endpoints, "clusters": result.clusters,
                   "edges": result.edges, "unreachable": result.unreachable,
                   "estimated": result.estimated},
                  file, separators=(",", ":"))
    temp.replace(args.output)
    progress.log(f"Saved {len(result.endpoints):,} endpoints in {len(result.clusters):,} clusters "
                 f"and {len(result.edges):,} access edges to {args.output}")
    progress.log(f"Categories: {dict(Counter(e['category'] for e in result.endpoints.values()))}")
    estimated_edges = sum(1 for edge in result.edges.values() if edge["estimated"])
    progress.log(f"Access edges: {len(result.edges) - estimated_edges:,} along roads, "
                 f"{estimated_edges:,} estimated by distance "
                 f"({len(result.estimated):,} endpoints have no selected road path)")
    if result.unreachable:
        progress.log(f"{len(result.unreachable):,} endpoints have no network node within reach")
    progress.finish("endpoints saved")


if __name__ == "__main__":
    main()
