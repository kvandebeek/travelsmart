"""Consolidate all local Google Maps captures into shared provider observations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from travelsmart.config import ROOT
from travelsmart.google_maps_import import import_captures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT / "data")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "observations" / "google_maps" / "captures.jsonl")
    parser.add_argument("--delete-sources", action="store_true",
                        help="Remove capture folders after the combined file is written; stop collectors for those folders first")
    parser.add_argument("--keep-folder", type=Path, action="append", default=[],
                        help="Leave this capture folder in place during cleanup; repeat as needed")
    args = parser.parse_args()
    print(json.dumps(import_captures(source_root=args.source_root, output=args.output,
                                     delete_sources=args.delete_sources,
                                     keep_folders=tuple(args.keep_folder))))


if __name__ == "__main__":
    main()
