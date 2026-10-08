import json

from travelsmart.commute_export import export_commutes
from travelsmart.config import ROOT
from travelsmart.google_maps_import import import_captures


def _capture(moment: str, origin: str, destination: str, minutes: int) -> dict:
    return {"captured_at": moment, "origin": origin, "destination": destination,
            "origin_location": f"{origin}, Belgium", "destination_location": f"{destination}, Belgium",
            "travel_time_minutes": minutes, "distance_km": 12.4, "status": "ok"}


def test_import_is_repeatable_and_export_uses_google_observations(tmp_path):
    source = tmp_path / "data"
    first = source / "google_maps_a_captures" / "captures.jsonl"
    second = source / "google_maps_b_captures" / "captures.jsonl"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    a = _capture("2026-10-08T09:00:00+02:00", "Aalst", "Brussels", 35)
    b = _capture("2026-10-08T09:15:00+02:00", "Brussels", "Aalst", 38)
    first.write_text(json.dumps(a) + "\n" + json.dumps(b) + "\n", encoding="utf-8")
    second.write_text(json.dumps(a) + "\n", encoding="utf-8")
    observations = tmp_path / "observations"
    target = observations / "google_maps" / "captures.jsonl"
    result = import_captures(source_root=source, output=target)
    assert result["source_files"] == 2 and result["new_observations"] == 2
    assert import_captures(source_root=source, output=target)["new_observations"] == 0
    rows = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    assert {row["direction"] for row in rows} == {"direct"}
    assert {row["duration_seconds"] for row in rows} == {2100, 2280}
    assert {row["distance_m"] for row in rows} == {12400}
    assert all(row["freeflow_seconds"] is None for row in rows)

    public = tmp_path / "public"
    result = export_commutes(config_dir=ROOT / "config", data_dir=observations, output_dir=public)
    month = json.loads((public / "2026-10.json").read_text(encoding="utf-8"))
    index = json.loads((public / "index.json").read_text(encoding="utf-8"))
    latest = json.loads((public / "latest.json").read_text(encoding="utf-8"))
    assert result["google_routes"] == 2
    assert {row["route_id"] for row in month} == {route["id"] for route in index["routes"]
                                                 if route["tier"] == "google"}
    assert all(row["provider"] == "google_maps" for row in latest["calls"])


def test_cleanup_removes_only_imported_capture_folders(tmp_path):
    source = tmp_path / "data"
    folder = source / "google_maps_a_captures"
    folder.mkdir(parents=True)
    (folder / "captures.jsonl").write_text(json.dumps(_capture("2026-10-08T09:00:00+02:00", "Aalst", "Brussels", 35)) + "\n", encoding="utf-8")
    (folder / "google_maps.png").write_bytes(b"screenshot")
    profile = source / "google_maps_a_profile"
    profile.mkdir()
    (profile / "Preferences").write_text("keep", encoding="utf-8")
    target = tmp_path / "observations" / "google_maps" / "captures.jsonl"
    result = import_captures(source_root=source, output=target, delete_sources=True)
    assert result["deleted_folders"] == 1 and target.exists()
    assert not folder.exists() and (profile / "Preferences").exists()
