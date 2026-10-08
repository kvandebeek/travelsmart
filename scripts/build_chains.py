"""Cut the measured network into provider-sized chains (docs/network-design.md §6.1)."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from travelsmart.measure.chains import MAX_LEGS, MEASURED_KINDS, build_chains


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", type=Path, default=Path("data/network/network.json"))
    parser.add_argument("--output", type=Path, default=Path("data/network/chains.json"))
    parser.add_argument("--max-legs", type=int, default=MAX_LEGS)
    args = parser.parse_args()

    network = json.loads(args.network.read_text(encoding="utf-8"))
    chains = build_chains(network["edges"], max_legs=args.max_legs)
    legs = [len(chain["edges"]) for chain in chains]
    measured = sum(1 for edge in network["edges"].values() if edge["kind"] in MEASURED_KINDS)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(".partial")
    with temp.open("w", encoding="utf-8") as file:
        json.dump({"source": str(args.network), "max_legs": args.max_legs, "chains": chains},
                  file, separators=(",", ":"))
    temp.replace(args.output)

    print(f"{len(chains):,} chains cover {sum(legs):,} of {measured:,} measured edges", file=sys.stderr)
    print(f"legs per chain: median {statistics.median(legs):.0f}, mean {statistics.mean(legs):.1f}, "
          f"max {max(legs)}", file=sys.stderr)
    print(f"kinds measured: {dict(Counter(network['edges'][e]['kind'] for c in chains for e in c['edges']))}",
          file=sys.stderr)
    print(f"one pass over every chain costs {len(chains):,} requests", file=sys.stderr)


if __name__ == "__main__":
    main()
