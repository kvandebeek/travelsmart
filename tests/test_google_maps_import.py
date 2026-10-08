import json

from travelsmart.google_maps_import import import_captures


def _capture(moment: str, origin: str, destination: str, minutes: int) -> dict:
    return {"captured_at": moment, "origin": origin, "destination": destination,
            "origin_location": f"{origin}, Belgium", "destination_location": f"{destination}, Belgium",
            "travel_time_minutes": minutes, "distance_km": 12.4, "status": "ok"}


def test_import_is_repeatable(tmp_path):
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


def test_cleanup_can_keep_a_running_collector_folder(tmp_path):
    source = tmp_path / "data"
    active = source / "google_maps_captures"
    inactive = source / "google_maps_other_captures"
    for folder in (active, inactive):
        folder.mkdir(parents=True)
        (folder / "captures.jsonl").write_text(
            json.dumps(_capture("2026-10-08T09:00:00+02:00", "Aalst", "Brussels", 35)) + "\n",
            encoding="utf-8")
    result = import_captures(source_root=source,
                             output=tmp_path / "observations" / "google_maps" / "captures.jsonl",
                             delete_sources=True, keep_folders=(active,))
    assert result["deleted_folders"] == 1 and result["kept_folders"] == 1
    assert active.exists() and not inactive.exists()


def test_import_keeps_every_route_option_and_backfills_roads(tmp_path):
    source = tmp_path / "data" / "google_maps_a_captures"
    source.mkdir(parents=True)
    capture = _capture("2026-10-08T09:00:00+02:00", "Lokeren", "Sint-Niklaas", 24)
    capture.update({"via": "E17", "routes": [
        {"rank": 0, "travel_time_minutes": 24, "distance_km": 21.1, "via": "E17", "note": "Fastest route"},
        {"rank": 1, "travel_time_minutes": 26, "distance_km": 20.2, "via": "N70", "note": None},
        {"rank": 2, "travel_time_minutes": 27, "distance_km": 15.6, "via": "Rozenstraat/N473 and N70", "note": None}]})
    (source / "captures.jsonl").write_text(json.dumps(capture) + "\n", encoding="utf-8")
    target = tmp_path / "observations" / "google_maps" / "captures.jsonl"
    # An observation imported before roads were recorded: its road comes from the stored card text.
    old = {"capture_id": "old", "observed_at": "2026-10-08T08:00:00+02:00",
           "route_card_text": "21 min 26.2 km via E40 Best route now due to traffic conditions Details Preview"}
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(old) + "\n", encoding="utf-8")
    import_captures(source_root=tmp_path / "data", output=target)
    rows = {row["capture_id"]: row for row in map(json.loads, target.read_text(encoding="utf-8").splitlines())}
    assert (rows["old"]["via"], rows["old"]["roads"]) == ("E40", ["E40"])
    new = next(row for key, row in rows.items() if key != "old")
    assert (new["via"], new["roads"], new["duration_seconds"]) == ("E17", ["E17"], 1440)
    assert [(a["rank"], a["roads"], a["duration_seconds"], a["distance_m"]) for a in new["alternatives"]] == [
        (1, ["N70"], 1560, 20200), (2, ["N473", "N70"], 1620, 15600)]
