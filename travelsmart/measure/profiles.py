"""Turn raw leg measurements into a profile per edge (docs/network-design.md §8).

A profile is 7 weekdays x 48 half hours of cells. Each cell holds how many measurements fell in it,
their median travel time and their spread, so a thin cell is visibly thin rather than quietly wrong.

Two numbers make the rest readable:

- the **usual** time is the median of the half-hour medians, counting each half hour once, so a
  stretch measured far more often at rush hour does not drag its own baseline upwards;
- **compared with usual** is a cell's median over that usual time, minus one: +0.3 means this half
  hour normally takes 30% longer than the edge's own normal.

Providers disagree about level more than about shape. TomTom is the reference, and another provider
is scaled by its median ratio to TomTom on the same edge before being merged, so Google's rounded
minutes move an edge's shape without moving its level. What is left of the disagreement is kept as
the edge's uncertainty.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field

HALF_HOURS = 48
WEEKDAYS = 7
REFERENCE_PROVIDER = "tomtom"
MIN_CELL_COUNT = 3          # fewer than this and a cell falls back rather than being trusted


@dataclass
class Cell:
    count: int
    median: float
    spread: float           # half the interquartile range, 0 when there is too little to tell

    def as_dict(self) -> dict:
        return {"count": self.count, "median": round(self.median, 1), "spread": round(self.spread, 1)}


@dataclass
class Profile:
    edge_id: str
    provider: str
    cells: dict[tuple[int, int], Cell] = field(default_factory=dict)
    usual: float = 0.0

    def as_dict(self) -> dict:
        return {"edge": self.edge_id, "provider": self.provider, "usual": round(self.usual, 1),
                "cells": {f"{weekday}:{half_hour}": cell.as_dict()
                          for (weekday, half_hour), cell in sorted(self.cells.items())}}


def _cell(values: list[float]) -> Cell:
    if len(values) >= 4:
        quartiles = statistics.quantiles(values, n=4)
        spread = (quartiles[2] - quartiles[0]) / 2
    else:
        spread = 0.0
    return Cell(count=len(values), median=statistics.median(values), spread=spread)


def build_profile(edge_id: str, provider: str, samples: list[tuple[int, int, float]]) -> Profile:
    """One edge's profile from (weekday, half hour, seconds) samples, which must be accepted ones."""
    grouped: dict[tuple[int, int], list[float]] = defaultdict(list)
    for weekday, half_hour, seconds in samples:
        grouped[(weekday % WEEKDAYS, half_hour % HALF_HOURS)].append(seconds)
    profile = Profile(edge_id=edge_id, provider=provider,
                      cells={key: _cell(values) for key, values in grouped.items()})
    profile.usual = usual_time(profile)
    return profile


def usual_time(profile: Profile) -> float:
    """The median of the half-hour medians, so heavily measured hours do not dominate."""
    by_half_hour: dict[int, list[float]] = defaultdict(list)
    for (_weekday, half_hour), cell in profile.cells.items():
        by_half_hour[half_hour].append(cell.median)
    if not by_half_hour:
        return 0.0
    return statistics.median([statistics.median(values) for values in by_half_hour.values()])


def compared_with_usual(profile: Profile, weekday: int, half_hour: int) -> float | None:
    """How this cell compares with the edge's usual time: 0.3 means 30% slower than normal."""
    seconds = lookup(profile, weekday, half_hour)
    if seconds is None or not profile.usual:
        return None
    return seconds / profile.usual - 1


def lookup(profile: Profile, weekday: int, half_hour: int, min_count: int = MIN_CELL_COUNT) -> float | None:
    """The travel time for a moment, falling back when a cell is thin (§8.5).

    That weekday's cell, then every day's cell for the same half hour, then the usual time. A thin
    cell is still used when nothing better exists, because some measurement beats none.
    """
    cell = profile.cells.get((weekday % WEEKDAYS, half_hour % HALF_HOURS))
    if cell and cell.count >= min_count:
        return cell.median
    # Falling back *past* a thin cell, so its own handful of measurements must not come along and
    # drag the answer with them: other days count only where they are themselves well measured.
    same_half_hour = [other.median for (_weekday, other_half), other in profile.cells.items()
                      if other_half == half_hour % HALF_HOURS and other is not cell
                      and other.count >= min_count]
    if same_half_hour:
        return statistics.median(same_half_hour)
    if cell:
        return cell.median
    return profile.usual or None


def provider_ratio(profile: Profile, reference: Profile) -> float | None:
    """How this provider's times compare with the reference's, over the cells they share.

    None when they share too little to tell, in which case the provider cannot be merged in.
    """
    ratios = [profile.cells[key].median / reference.cells[key].median
              for key in profile.cells.keys() & reference.cells.keys()
              if reference.cells[key].median > 0]
    return statistics.median(ratios) if ratios else None


def combine(profiles: list[Profile], reference_provider: str = REFERENCE_PROVIDER) -> dict:
    """Merge one edge's per-provider profiles onto the reference's level (§8.4).

    Returns the merged cells, the usual time, and the disagreement left between providers, which is
    this edge's uncertainty rather than something to average away.
    """
    if not profiles:
        return {}
    by_provider = {profile.provider: profile for profile in profiles}
    reference = by_provider.get(reference_provider) or max(profiles, key=lambda p: len(p.cells))
    scaled: dict[tuple[int, int], list[float]] = defaultdict(list)
    ratios, unscalable = {}, []
    for profile in profiles:
        if profile is reference:
            ratio = 1.0
        else:
            ratio = provider_ratio(profile, reference)
            if ratio is None or ratio <= 0:
                unscalable.append(profile.provider)
                continue
        ratios[profile.provider] = round(ratio, 4)
        for key, cell in profile.cells.items():
            scaled[key].append(cell.median / ratio)
    merged = {key: _cell(values) for key, values in scaled.items()}
    combined = Profile(edge_id=reference.edge_id, provider="combined", cells=merged)
    combined.usual = usual_time(combined)
    # How far apart the providers stayed after scaling, as a share of the usual time.
    disagreements = [cell.spread / combined.usual for cell in merged.values()
                     if cell.count > 1 and combined.usual]
    return {**combined.as_dict(), "providers": ratios,
            "not_merged": unscalable,
            "uncertainty": round(statistics.median(disagreements), 4) if disagreements else 0.0}
