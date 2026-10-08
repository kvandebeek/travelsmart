"""Cells, the usual time, the fallbacks when a cell is thin, and merging providers (§8)."""

from travelsmart.measure.profiles import (Profile, build_profile, combine, compared_with_usual,
                                          lookup, provider_ratio, usual_time)


def samples(*rows):
    return [(weekday, half_hour, seconds) for weekday, half_hour, seconds in rows]


def test_a_cell_holds_the_count_median_and_spread():
    profile = build_profile("e1", "tomtom", samples((1, 14, 100), (1, 14, 120), (1, 14, 110)))
    cell = profile.cells[(1, 14)]
    assert cell.count == 3 and cell.median == 110
    assert cell.spread == 0            # too few values to say anything about spread


def test_the_usual_time_counts_each_half_hour_once():
    # The 07:30 half hour is measured five times and is slow; 10:00 once and is quick. The usual
    # time must sit between them, not be dragged to the rush-hour value by its own weight.
    rush = [(0, 15, 300)] * 5
    quiet = [(0, 20, 100)]
    profile = build_profile("e1", "tomtom", samples(*rush, *quiet))
    assert profile.usual == 200        # median of [300, 100], not of all six measurements


def test_compared_with_usual_reads_as_a_share():
    profile = build_profile("e1", "tomtom", samples(*[(0, 15, 300)] * 3, *[(0, 20, 100)] * 3))
    assert profile.usual == 200
    assert compared_with_usual(profile, 0, 15) == 0.5     # 50% slower than its own normal
    assert compared_with_usual(profile, 0, 20) == -0.5


def test_a_thin_cell_falls_back_on_the_same_half_hour_on_other_days():
    profile = build_profile("e1", "tomtom", samples(
        *[(0, 15, 300)] * 3, *[(1, 15, 320)] * 3, (2, 15, 999)))
    # Tuesday's 07:30 has a single wild measurement, so the half hour across days answers instead.
    assert lookup(profile, 2, 15) == 310
    assert lookup(profile, 0, 15) == 300                  # a full cell answers for itself


def test_an_unmeasured_moment_falls_back_on_the_usual_time():
    profile = build_profile("e1", "tomtom", samples(*[(0, 15, 300)] * 3))
    assert lookup(profile, 4, 40) == profile.usual
    assert lookup(Profile("e1", "tomtom"), 0, 0) is None   # nothing measured at all


def test_a_single_thin_cell_is_still_better_than_nothing():
    profile = build_profile("e1", "tomtom", samples((3, 9, 250)))
    assert lookup(profile, 3, 9) == 250


def test_provider_ratio_compares_only_the_cells_they_share():
    reference = build_profile("e1", "tomtom", samples((0, 10, 100), (0, 11, 200)))
    other = build_profile("e1", "google", samples((0, 10, 120), (0, 11, 240), (0, 12, 999)))
    assert provider_ratio(other, reference) == 1.2
    assert provider_ratio(build_profile("e1", "here", samples((5, 5, 1))), reference) is None


def test_combining_lifts_another_provider_onto_the_reference_level():
    # Google reads 20% high everywhere. After scaling it must agree with TomTom, not inflate it.
    reference = build_profile("e1", "tomtom", samples((0, 10, 100), (0, 11, 200)))
    google = build_profile("e1", "google", samples((0, 10, 120), (0, 11, 240)))
    merged = combine([reference, google])
    assert merged["providers"] == {"tomtom": 1.0, "google": 1.2}
    assert merged["cells"]["0:10"]["median"] == 100
    assert merged["cells"]["0:11"]["median"] == 200
    assert merged["usual"] == 150


def test_a_provider_sharing_no_cells_is_reported_rather_than_merged():
    reference = build_profile("e1", "tomtom", samples((0, 10, 100)))
    stranger = build_profile("e1", "here", samples((4, 40, 900)))
    merged = combine([reference, stranger])
    assert merged["not_merged"] == ["here"]
    assert merged["cells"] == {"0:10": {"count": 1, "median": 100.0, "spread": 0.0}}


def test_disagreement_between_providers_is_kept_as_uncertainty():
    reference = build_profile("e1", "tomtom", samples((0, 10, 100), (0, 11, 100)))
    # Same level overall, but it disagrees about which half hour is slow.
    other = build_profile("e1", "google", samples((0, 10, 150), (0, 11, 50)))
    merged = combine([reference, other])
    assert merged["uncertainty"] >= 0
    assert combine([reference])["uncertainty"] == 0.0     # one provider never disagrees


def test_combining_nothing_gives_nothing():
    assert combine([]) == {}
