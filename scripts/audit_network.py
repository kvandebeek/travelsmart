"""Audit a generated network and write a list of likely topology problems."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from travelsmart.network.audit import audit_network
from travelsmart.network.osm import EXTRACT


def write_audit(network_path: Path, source_ways: dict | None = None,
                source_points: dict | None = None, update=None, log=print) -> Path:
    network = json.loads(network_path.read_text(encoding="utf-8"))
    if source_ways is None:
        source_cache = EXTRACT.with_suffix(".roads.json")
        if source_cache.exists():
            source = json.loads(source_cache.read_text(encoding="utf-8"))
            selected = {str(way_id) for way_id in network.get("source_way_ids", source["ways"])}
            source_ways = {way_id: way for way_id, way in source["ways"].items()
                           if way_id in selected}
            source_points = source["points"]
    result = audit_network(network["nodes"], network["edges"], source_ways, source_points, update=update)
    output = network_path.with_name("audit.json")
    partial = output.with_suffix(".partial")
    partial.write_text(json.dumps(result, separators=(",", ":")), encoding="utf-8")
    partial.replace(output)
    log(f"Saved {len(result['issues'])} flagged locations to {output}")
    log(str(result["summary"]))
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("network", type=Path, nargs="?", default=Path("data/network/network.json"))
    write_audit(parser.parse_args().network)
