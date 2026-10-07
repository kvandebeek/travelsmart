from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from travelsmart.analytics import best_moments, best_round_trips, grouped_statistics, percentile, schedule_slots, statistics
from travelsmart.config import load_settings
from travelsmart.db import Measurement, make_session_factory
from travelsmart.export import export_static
from travelsmart.providers.osrm import OSRMProvider

def test_configuration_and_directionality():
    settings = load_settings()
    assert settings.locations["diepenbeek"].display_name == "Diepenbeek"
    assert settings.journeys["diepenbeek_brussels"].origin == "diepenbeek"
    assert settings.journeys["brussels_diepenbeek"].origin == "brussels"
    n = len(settings.locations)
    assert n >= 50 and len(settings.journeys) == n * (n - 1)
    assert all("_" not in key for key in settings.locations)  # journey ids are <from>_<to>
    assert settings.locations["hasselt"].province == "Limburg"

def test_percentiles_and_statistics():
    values = [10, 20, 30, 40, 50]
    result = statistics(values)
    assert percentile(values, .1) == 14
    assert result["median_seconds"] == 30
    assert result["p90_seconds"] == 46
    assert result["best_seconds"] == result["p10_seconds"]

def test_provider_normalizes_osrm_result(monkeypatch):
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"code":"Ok", "routes":[{"distance":1234, "duration":567}]}
    monkeypatch.setattr("travelsmart.providers.osrm.httpx.get", lambda *a, **k: Response())
    cfg = load_settings(); result = OSRMProvider("http://local").calculate_route(cfg.locations["diepenbeek"], cfg.locations["brussels"])
    assert result.distance_m == 1234 and result.duration_seconds == 567

def test_export_structure(tmp_path):
    settings = load_settings(); Session = make_session_factory(f"sqlite:///{tmp_path / 'test.db'}")
    now = datetime.now(timezone.utc)
    with Session() as s:
        s.add(Measurement(journey_id="diepenbeek_brussels", timestamp_utc=now, timestamp_local=now, weekday=0, departure_time="08:00", provider="mock", distance_m=1, duration_seconds=3600, measurement_type="baseline", created_at=now))
        s.commit(); export_static(s, settings, tmp_path / "export")
    assert (tmp_path / "export" / "index.json").exists()
    assert (tmp_path / "export" / "journeys" / "diepenbeek_brussels.json").exists()

def test_weekday_slot_statistics_and_schedule_slots():
    rows = [SimpleNamespace(duration_seconds=d, weekday=1, departure_time=t) for d, t in [(100, "12:03"), (200, "12:14"), (900, "12:15")]]
    stats = grouped_statistics(rows)["by_weekday_slot"]["1"]
    assert stats["12:00"]["samples"] == 2 and stats["12:00"]["median_seconds"] == 150
    assert stats["12:15"]["samples"] == 1
    slots = schedule_slots(load_settings().schedules)
    assert slots[0] == "05:30" and "09:15" in slots and "09:30" not in slots and "15:00" in slots and slots[-1] == "18:45"

def test_collect_many_is_parallel_and_tolerates_failures(tmp_path):
    from travelsmart.collectors import collect_many
    from travelsmart.providers import RouteResult
    settings = load_settings(); Session = make_session_factory(f"sqlite:///{tmp_path / 'many.db'}")
    class Fake:
        name = "fake"
        def calculate_route(self, origin, destination, departure_time=None, via=()):
            if destination.id == "brussels": raise RuntimeError("boom")
            return RouteResult(1000, 600)
    ids = ["diepenbeek_antwerp", "diepenbeek_brussels", "antwerp_ghent", "ghent_leuven"]
    with Session() as s:
        rows, failed = collect_many(s, settings, Fake(), ids, workers=3)
        stored = s.query(Measurement).count()
    assert failed == ["diepenbeek_brussels"] and len(rows) == stored == 3
    assert len({r.timestamp_utc for r in rows}) == 1

def test_best_moments_ranks_quarter_hours_without_durations():
    def slot(mean, n=2, sd=None):
        return {"samples": n, "mean_seconds": mean, **({"standard_deviation_seconds": sd} if sd is not None else {})}
    data = {"1": {"08:00": slot(1250), "08:15": slot(1000), "08:30": slot(1300), "09:00": slot(1100)}, "4": {"16:30": slot(1500)}}
    result = best_moments(data, min_samples=1, min_spread=0.03)
    assert result["meaningful"] and result["best"] == {"day": 1, "slot": "08:15", "end": "08:30"}
    assert result["worst"] == {"day": 4, "slot": "16:30", "end": "16:45"}
    assert result["cells"]["1"]["08:15"]["level"] == 1 and result["cells"]["4"]["16:30"]["level"] == 5
    assert result["cells"]["1"]["08:15"]["level"] < result["cells"]["1"]["08:30"]["level"]  # 08:15 beats 08:30
    assert result["best_by_day"] == {"1": "08:15", "4": "16:30"}
    assert "mean_seconds" not in str(result)

def test_best_moments_window_covers_equally_good_neighbours_and_ignores_noise():
    def slot(mean): return {"samples": 5, "mean_seconds": mean, "standard_deviation_seconds": 5}
    plateau = {"1": {"10:00": slot(1000), "10:15": slot(1004), "10:30": slot(1008), "10:45": slot(1003), "11:00": slot(1200)}}
    result = best_moments(plateau)
    assert result["best"] == {"day": 1, "slot": "10:00", "end": "11:00"}  # whole flat run, not one lucky slot
    noisy = {"1": {"10:00": {"samples": 4, "mean_seconds": 1000, "standard_deviation_seconds": 300},
                   "10:15": {"samples": 4, "mean_seconds": 1040, "standard_deviation_seconds": 300}}}
    assert not best_moments(noisy)["meaningful"]  # 4% gap is within the sampling noise
    flat = best_moments({"1": {"08:00": slot(1000), "10:00": slot(1010)}}, min_spread=0.03)
    assert not flat["meaningful"] and flat["best"] is None and flat["cells"] == {} and flat["covered_cells"] == 2
    assert not best_moments({"1": {"08:00": slot(1000)}})["meaningful"]

def test_best_round_trip_accounts_for_time_on_the_road_and_the_stay():
    def slot(mean): return {"samples": 5, "mean_seconds": mean, "standard_deviation_seconds": 5}
    # Leaving at 07:xx takes 1h and returns at 16:xx; leaving at 08:xx takes 1.5h and returns at 17:3x (8h stay).
    out = {"1": {"07:00": slot(3600), "07:15": slot(3600), "08:00": slot(5400), "08:15": slot(5400)}}
    back = {"1": {"16:00": slot(3600), "16:15": slot(3600), "17:30": slot(5400), "17:45": slot(5400)}}
    result = best_round_trips(out, back, work_hours=[8])["8"]
    assert result["meaningful"]
    assert result["best"]["out"] == {"slot": "07:00", "end": "07:30"} and result["best"]["back"] == {"slot": "16:00", "end": "16:30"}
    assert result["worst"]["out"]["slot"] == "08:00" and result["worst"]["back"]["slot"] == "17:30"
    assert result["days"]["1"]["level"] == 1
    assert "seconds" not in str(result)
    assert not best_round_trips(out, {"1": {"23:00": slot(3600)}}, work_hours=[8])["8"]["meaningful"]  # nothing to compare

def test_verified_fetch_keeps_every_check_on(tmp_path):
    import ssl
    from travelsmart.config import ROOT
    from travelsmart.verified_fetch import verified_context
    context = verified_context(ROOT / "config" / "certs" / "digicert-global-g2-tls-rsa-sha256-2020-ca1.pem")
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED

def _miv_xml(published, lanes):
    body = "".join(
        f'<meetpunt beschrijvende_id="{lane_id}" unieke_id="{lane_id}"><tijd_waarneming>{observed}</tijd_waarneming>'
        f'<beschikbaar>1</beschikbaar><defect>0</defect><geldig>{valid}</geldig>'
        + "".join(f'<meetdata klasse_id="{i}"><verkeersintensiteit>{n}</verkeersintensiteit><voertuigsnelheid_rekenkundig>{v}</voertuigsnelheid_rekenkundig>'
                  f'<voertuigsnelheid_harmonisch>{v}</voertuigsnelheid_harmonisch></meetdata>' for i, (n, v) in enumerate(classes, 1))
        + "</meetpunt>" for lane_id, observed, valid, classes in lanes)
    return f"<miv><tijd_publicatie>{published}</tijd_publicatie><tijd_laatste_config_wijziging>2025-12-05T12:25:29+01:00</tijd_laatste_config_wijziging>{body}</miv>".encode()

def test_miv_ingest_filters_stale_invalid_and_folds_running_statistics(tmp_path):
    from travelsmart import miv
    from travelsmart.db import SpeedCell
    Session = make_session_factory(f"sqlite:///{tmp_path / 'miv.db'}")
    obs = "2026-10-06T08:07:00+02:00"            # a Tuesday morning, Belgian time -> weekday 1, slot 08:00
    quiet = [(0, 252)] * 5
    lanes = [("H100L10", obs, 1, [(0, 252), (10, 100), (0, 252), (2, 80), (0, 252)]),   # two lanes of one site are combined
             ("H100L20", obs, 1, [(0, 252), (5, 120), (0, 252), (0, 252), (0, 252)]),
             ("H200L10", "2026-10-05T18:57:00+01:00", 1, [(0, 252), (9, 90), (0, 252), (0, 252), (0, 252)]),  # stale
             ("H300L10", obs, 0, [(0, 252), (9, 90), (0, 252), (0, 252), (0, 252)]),                            # not valid
             ("H400L10", obs, 1, quiet)]                                                                       # no traffic
    sites = {"H100L10": "A1@1", "H100L20": "A1@1", "H200L10": "A2@2", "H300L10": "A3@3", "H400L10": "A4@4"}
    with Session() as s:
        counters = miv.ingest(s, _miv_xml("2026-10-06T08:08:00+02:00", lanes + [("H999L10", obs, 1, quiet)]), sites)
        assert counters == {"sites": 4, "unmapped": 1, "stale": 1, "invalid": 1, "no_traffic": 1, "duplicate": 0, "stored": 1}
        cell = s.get(SpeedCell, ("A1@1", 1, "08:00"))
        assert cell.readings == 1 and cell.weight == 17
        assert abs(3600 / cell.mean_spk - 102.0) < 0.01   # flow-weighted harmonic mean of 100/80/120 km/h
        # the same observation again is a duplicate, a later one in the same slot is folded in with its own weight
        assert miv.ingest(s, _miv_xml("2026-10-06T08:09:00+02:00", lanes), sites)["duplicate"] == 1
        later = [("H100L10", "2026-10-06T08:12:00+02:00", 1, [(0, 252), (17, 51), (0, 252), (0, 252), (0, 252)])]
        miv.ingest(s, _miv_xml("2026-10-06T08:13:00+02:00", later), sites)
        cell = s.get(SpeedCell, ("A1@1", 1, "08:00"))
        assert cell.readings == 2 and cell.weight == 34
        slow, fast = 3600 / 51, 3600 / 102.0
        assert abs(cell.mean_spk - (slow + fast) / 2) < 0.01 and cell.min_spk < cell.max_spk
        assert abs(cell.m2 / cell.weight - ((slow - fast) / 2) ** 2) < 0.01   # weighted variance of the two readings

def test_miv_config_groups_lanes_into_sites_and_reads_decimal_commas():
    from travelsmart import miv
    def lane(uid, road, km, lat="50,9828171", lon="5,25"):
        return (f'<meetpunt unieke_id="{uid}"><beschrijvende_id>X{uid}L10</beschrijvende_id><volledige_naam>E313 test</volledige_naam><Ident_8>{road}</Ident_8>'
                f'<Kmp_Rsys>{km}</Kmp_Rsys><lengtegraad_EPSG_4326>{lon}</lengtegraad_EPSG_4326><breedtegraad_EPSG_4326>{lat}</breedtegraad_EPSG_4326></meetpunt>')
    xml = ("<mivconfig><tijd_laatste_config_wijziging>T1</tijd_laatste_config_wijziging>" + lane(1, "A0010001", "10,371") + lane(2, "A0010001", "10,371", lat="50,9828173")
           + lane(3, "A0010002", "10,371") + lane(4, "A0020001", "3,0", lat="", lon="") + lane(5, "NULL", "0") + lane(6, "NULL", "0") + "</mivconfig>").encode()
    changed, lane_sites, sites = miv.parse_config(xml)
    assert changed == "T1"
    assert lane_sites == {"1": "A0010001@10,371", "2": "A0010001@10,371", "3": "A0010002@10,371",   # lane 4 has no position: dropped
                          "5": "lane@5", "6": "lane@6"}                                       # no road information: never merged
    assert sites["A0010001@10,371"]["lanes"] == 2 and abs(sites["A0010001@10,371"]["lat"] - 50.9828172) < 1e-6

def test_miv_dedupe_compares_instants_across_utc_offsets(tmp_path):
    """The feed mixes +01:00 and +02:00 and SQLite drops offsets: duplicates must be judged on the real instant."""
    from travelsmart import miv
    from travelsmart.db import SiteLatest, SpeedCell
    Session = make_session_factory(f"sqlite:///{tmp_path / 'dst.db'}")
    sites = {"H100L10": "A1@1"}
    lane = lambda observed, cars: [("H100L10", observed, 1, [(0, 252), (cars, 100), (0, 252), (0, 252), (0, 252)])]
    with Session() as s:
        assert miv.ingest(s, _miv_xml("2026-10-06T08:08:00+01:00", lane("2026-10-06T08:07:00+01:00", 10)), sites)["stored"] == 1  # 07:07 UTC
        # 09:05+02:00 is 07:05 UTC: two minutes OLDER than what is stored, even though its wall-clock time is later
        assert miv.ingest(s, _miv_xml("2026-10-06T09:06:00+02:00", lane("2026-10-06T09:05:00+02:00", 10)), sites)["duplicate"] == 1
        # 09:10+02:00 is 07:10 UTC: newer, so it is stored
        assert miv.ingest(s, _miv_xml("2026-10-06T09:11:00+02:00", lane("2026-10-06T09:10:00+02:00", 10)), sites)["stored"] == 1
        assert s.get(SpeedCell, ("A1@1", 1, "09:00")).readings == 2   # both Belgian-time 09:0x readings land in the 09:00 slot
    with Session() as s:
        stored = s.get(SiteLatest, "A1@1").observed_utc
        assert stored.replace(tzinfo=None) == datetime(2026, 10, 6, 7, 10)  # stored as UTC

def test_lambert72_matches_the_epsg_example_and_shifts_to_wgs84():
    from travelsmart.lambert72 import to_bd72, to_wgs84
    lat, lon = to_bd72(251763.20, 153034.13)  # EPSG guidance note 7-2: 50°40'46.461"N 5°48'26.533"E on BD72
    assert abs(lat - (50 + 40 / 60 + 46.461 / 3600)) < 1e-6 and abs(lon - (5 + 48 / 60 + 26.533 / 3600)) < 1e-6
    wlat, wlon = to_wgs84(251763.20, 153034.13)
    shift_m = ((wlat - lat) * 111200) ** 2 + ((wlon - lon) * 70500) ** 2
    assert 80 ** 2 < shift_m < 130 ** 2  # the BD72 -> WGS84 datum shift is about 100 m in Belgium

def _datex_xml(published, records):
    """records: (record id, xsi type, version, start, extra XML inside the record)."""
    body = "".join(
        f'<ns4:situation id="EVT{rid}"><ns2:situationRecord xsi:type="ns2:{kind}" id="{rid}" version="{version}">'
        f'<ns2:situationRecordVersionTime>{published}</ns2:situationRecordVersionTime>'
        f'<ns2:validity><validityStatus>active</validityStatus><validityTimeSpecification><overallStartTime>{start}</overallStartTime>'
        f'</validityTimeSpecification></ns2:validity>{extra}</ns2:situationRecord></ns4:situation>' for rid, kind, version, start, extra in records)
    return ('<ns4:payload xmlns="http://datex2.eu/schema/3/common" xmlns:ns2="http://datex2.eu/schema/3/situation" '
            'xmlns:ns3="http://datex2.eu/schema/3/locationReferencing" xmlns:ns4="http://datex2.eu/schema/3/d2Payload" '
            f'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><publicationTime>{published}</publicationTime>{body}</ns4:payload>').encode()

def _jam(queue, pos_list):
    return (f'<ns2:locationReference xsi:type="ns3:SingleRoadLinearLocation"><ns3:gmlLineString srsName="EPSG:31370">'
            f'<ns3:posList>{pos_list}</ns3:posList></ns3:gmlLineString><ns3:alertCLinear><ns3:alertCDirection><ns3:alertCDirectionCoded>'
            f'positive</ns3:alertCDirectionCoded></ns3:alertCDirection><ns3:alertCMethod4PrimaryPointLocation><ns3:alertCLocation>'
            f'<ns3:specificLocation>11108</ns3:specificLocation></ns3:alertCLocation></ns3:alertCMethod4PrimaryPointLocation></ns3:alertCLinear>'
            f'</ns2:locationReference><ns2:abnormalTrafficType>queuingTraffic</ns2:abnormalTrafficType><ns2:queueLength>{queue}</ns2:queueLength>')

ROADWORKS = ("W1", "MaintenanceWorks", 1, "2026-09-01T06:00:00+02:00",
             '<ns2:locationReference><ns3:pointByCoordinates><ns3:pointCoordinates><ns3:latitude>51.2</ns3:latitude>'
             '<ns3:longitude>4.4</ns3:longitude></ns3:pointCoordinates></ns3:pointByCoordinates></ns2:locationReference>'
             '<ns2:roadMaintenanceType>roadworks</ns2:roadMaintenanceType>')

def test_datex_tracks_jam_lifetime_length_and_geometry(tmp_path):
    import json
    from travelsmart import datex
    from travelsmart.db import JamObservation, TrafficEvent
    Session = make_session_factory(f"sqlite:///{tmp_path / 'datex.db'}")
    now = lambda hhmm: datetime.fromisoformat(f"2026-10-07T{hhmm}:30+02:00")
    start = "2026-10-07T08:58:00+02:00"
    with Session() as s:
        first = datex.ingest(s, _datex_xml("2026-10-07T09:00:00+02:00", [("J1", "AbnormalTraffic", 3, start, _jam(500, "155215 210996 155515 211396")), ROADWORKS]), now("09:00"))
        assert first == {"status": "stored", "records": 2, "jams": 1, "new": 2, "updated": 0, "ended": 0, "reopened": 0}
        # longer and re-versioned: the geometry follows the longest queue; a shorter one later does not replace it
        datex.ingest(s, _datex_xml("2026-10-07T09:02:00+02:00", [("J1", "AbnormalTraffic", 5, start, _jam(900, "155215 210996 155815 211796")), ROADWORKS]), now("09:02"))
        third = datex.ingest(s, _datex_xml("2026-10-07T09:04:00+02:00", [("J1", "AbnormalTraffic", 7, start, _jam(300, "155215 210996 155415 211196")), ROADWORKS]), now("09:04"))
        assert third["updated"] == 1 and third["ended"] == 0
        jam = s.get(TrafficEvent, "J1")
        assert (jam.kind, jam.subtype, jam.direction, jam.primary_location) == ("jam", "queuingTraffic", "positive", 11108)
        assert jam.queue_length_m == 300 and jam.max_queue_length_m == 900 and len(json.loads(jam.geometry)) == 2
        assert 50.5 < jam.min_lat <= jam.max_lat < 51.5 and 3.5 < jam.min_lon <= jam.max_lon < 4.5
        assert [o.queue_length_m for o in s.query(JamObservation).order_by(JamObservation.observed_utc)] == [500, 900, 300]
        # the jam leaves the feed: it has ended; the roadworks continue
        gone = datex.ingest(s, _datex_xml("2026-10-07T09:06:00+02:00", [ROADWORKS]), now("09:06"))
        assert gone["ended"] == 1 and gone["jams"] == 0
        jam = s.get(TrafficEvent, "J1")
        assert datex.as_utc(jam.gone_utc) == datetime(2026, 10, 7, 7, 6, tzinfo=timezone.utc)
        assert datex.as_utc(jam.last_seen_utc) == datetime(2026, 10, 7, 7, 4, tzinfo=timezone.utc)
        works = s.get(TrafficEvent, "W1")
        assert works.gone_utc is None and works.kind == "roadworks" and json.loads(works.geometry) == [[51.2, 4.4]]

def test_datex_never_closes_events_on_a_frozen_repeated_or_empty_feed(tmp_path):
    import pytest
    from travelsmart import datex
    from travelsmart.db import TrafficEvent
    Session = make_session_factory(f"sqlite:///{tmp_path / 'datex.db'}")
    with Session() as s:
        datex.ingest(s, _datex_xml("2026-10-07T09:00:00+02:00", [ROADWORKS]), datetime(2026, 10, 7, 7, 0, 30, tzinfo=timezone.utc))
        # the same publication again (also when written with another UTC offset) is skipped
        assert datex.ingest(s, _datex_xml("2026-10-07T08:00:00+01:00", []), datetime(2026, 10, 7, 7, 2, tzinfo=timezone.utc))["status"] == "duplicate"
        # a publication that stopped advancing is not trusted to say what has ended
        assert datex.ingest(s, _datex_xml("2026-10-07T09:01:00+02:00", []), datetime(2026, 10, 7, 8, 0, tzinfo=timezone.utc))["status"] == "stale"
        with pytest.raises(ValueError):
            datex.ingest(s, _datex_xml("2026-10-07T09:02:00+02:00", []), datetime(2026, 10, 7, 7, 2, 30, tzinfo=timezone.utc))
        assert s.get(TrafficEvent, "W1").gone_utc is None
