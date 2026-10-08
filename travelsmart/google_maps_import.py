"""Import local Google Maps browser captures into one observation file."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from travelsmart.config import ROOT


def capture_files(source_root: Path) -> list[Path]:
    """Find capture files without descending into Chromium profile directories."""
    found = []
    for folder, dirs, files in os.walk(source_root):
        dirs[:] = [name for name in dirs if not name.endswith("_profile")]
        if "captures.jsonl" in files:
            found.append(Path(folder) / "captures.jsonl")
    return sorted(found)


# Belgian road numbers in Google's "via" text: E- and A-motorways, N-roads and R-rings.
ROAD_CODE = re.compile(r"\b[EANR]\d{1,4}\b")
# Older captures only kept the whole card as one line: "21 min 26.2 km via E40 Best route now ...".
VIA_IN_CARD = re.compile(r"\bvia (.+?)(?= (?:Fastest|Best|Some|Heavy|Light|Moderate|Usual|Details|Preview)\b| \d+ (?:min|hr)\b|$)")


def road_codes(via: str | None) -> list[str]:
    """Road numbers in a Google "via" text, in order: "Rozenstraat/N473 and N70" -> ["N473", "N70"]."""
    return list(dict.fromkeys(ROAD_CODE.findall(via or "")))


def via_from_card(text: str | None) -> str | None:
    match = VIA_IN_CARD.search(text or "")
    return match.group(1) if match else None


def _with_roads(row: dict) -> dict:
    """Fill the recommended route's "via" text and road numbers on observations imported before they existed."""
    if "via" not in row:
        row["via"] = via_from_card(row.get("route_card_text"))
        row["roads"] = road_codes(row["via"])
    return row


def _identity(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def google_route_id(origin_location: str, destination_location: str) -> str:
    """Directed route ID for a pair of Google Maps locations, shared by every collector."""
    return "google:" + _identity(json.dumps([origin_location, destination_location], ensure_ascii=False))


def normalize_capture(raw: dict, source_file: str) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("capture must be a JSON object")
    observed = raw.get("captured_at")
    if not isinstance(observed, str) or datetime.fromisoformat(observed).tzinfo is None:
        raise ValueError("capture needs a timezone-aware captured_at")
    origin = raw.get("origin")
    destination = raw.get("destination")
    origin_location = raw.get("origin_location") or origin
    destination_location = raw.get("destination_location") or destination
    if not all(isinstance(value, str) and value.strip() for value in
               (origin, destination, origin_location, destination_location)):
        raise ValueError("capture needs both named endpoints")
    canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    capture_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    status = raw.get("status")
    duration = raw.get("travel_time_minutes")
    distance = raw.get("distance_km")
    if status == "ok" and (not isinstance(duration, (int, float)) or duration <= 0):
        raise ValueError("successful capture needs a positive travel_time_minutes")
    route_id = google_route_id(origin_location, destination_location)
    options = raw.get("routes")
    via = raw.get("via") or via_from_card(raw.get("route_card_text"))
    return {
        "provider": "google_maps", "account": None, "capture_id": capture_id,
        "route_id": route_id, "direction": "direct", "tier": "google",
        "scheduled_at": observed, "observed_at": observed,
        "status": "ok" if status == "ok" else "error",
        "error_type": None if status == "ok" else status,
        "distance_m": round(float(distance) * 1000) if distance is not None else None,
        "duration_seconds": round(float(duration) * 60) if duration is not None else None,
        "traffic_delay_seconds": None, "freeflow_seconds": None,
        "origin": origin, "destination": destination,
        "origin_id": raw.get("origin_id"), "destination_id": raw.get("destination_id"),
        "origin_location": origin_location, "destination_location": destination_location,
        "travel_time_text": raw.get("travel_time_text"),
        "route_card_text": raw.get("route_card_text"),
        # The recommended route's road, and the other routes Google listed at the same moment
        # (None: captured before the collector read them).
        "via": via, "roads": road_codes(via),
        "alternatives": None if options is None else [
            {"rank": option["rank"], "via": option.get("via"), "roads": road_codes(option.get("via")),
             "duration_seconds": round(option["travel_time_minutes"] * 60),
             "distance_m": round(option["distance_km"] * 1000) if option.get("distance_km") is not None else None,
             "note": option.get("note")}
            for option in options if option.get("rank") and option.get("travel_time_minutes")],
        "sweep_id": raw.get("sweep_id"), "pair_index": raw.get("pair_index"),
        "global_pair_index": raw.get("global_pair_index"),
        "corridor_id": raw.get("corridor_id"), "corridor_run_id": raw.get("corridor_run_id"),
        "leg_index": raw.get("leg_index"), "source_file": source_file,
    }


def _read_source(path: Path, source_root: Path) -> list[dict]:
    data = path.read_bytes()
    if data and not data.endswith(b"\n"):
        raise ValueError(f"{path}: last line is unfinished; wait for the collector to finish")
    source_file = path.relative_to(source_root).as_posix()
    rows = []
    for number, line in enumerate(data.decode("utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(normalize_capture(json.loads(line), source_file))
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            raise ValueError(f"{path}:{number}: {error}") from error
    return rows


def import_captures(*, source_root: Path = ROOT / "data",
                    output: Path = ROOT / "observations" / "google_maps" / "captures.jsonl",
                    delete_sources: bool = False,
                    keep_folders: tuple[Path, ...] = ()) -> dict:
    source_root = source_root.resolve()
    output = output.resolve()
    if not source_root.is_dir():
        raise ValueError(f"source folder does not exist: {source_root}")
    if output.is_relative_to(source_root):
        raise ValueError("output must be outside the source folder")
    protected = {folder.resolve() for folder in keep_folders}
    if any(not folder.is_relative_to(source_root) for folder in protected):
        raise ValueError("kept folders must be inside the source folder")
    files = capture_files(source_root)
    existing = {}
    if output.exists():
        for number, line in enumerate(output.read_text(encoding="utf-8").splitlines(), start=1):
            if line.strip():
                row = json.loads(line)
                if "capture_id" not in row:
                    raise ValueError(f"{output}:{number}: missing capture_id")
                existing[row["capture_id"]] = _with_roads(row)
    snapshots = {path: (path.stat().st_size, path.stat().st_mtime_ns) for path in files}
    added = 0
    for path in files:
        for row in _read_source(path, source_root):
            if row["capture_id"] not in existing:
                existing[row["capture_id"]] = row
                added += 1
    ordered = sorted(existing.values(), key=lambda row: (row["observed_at"], row["capture_id"]))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=output.parent,
                                     prefix=".google_maps_", suffix=".tmp", delete=False) as file:
        temporary = Path(file.name)
        for row in ordered:
            file.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    try:
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    deleted = 0
    if delete_sources:
        removable = [path for path in files if path.parent.resolve() not in protected]
        for path in removable:
            if not path.exists() or (path.stat().st_size, path.stat().st_mtime_ns) != snapshots[path]:
                raise ValueError(f"{path} changed during import; source folders were kept")
            if path.parent.resolve() == source_root or not path.parent.resolve().is_relative_to(source_root):
                raise ValueError(f"refusing to delete outside a capture subfolder: {path.parent}")
        for path in removable:
            shutil.rmtree(path.parent)
            deleted += 1
    return {"source_files": len(files), "new_observations": added,
            "total_observations": len(ordered), "deleted_folders": deleted,
            "kept_folders": len(files) - deleted if delete_sources else len(files),
            "output": str(output)}
