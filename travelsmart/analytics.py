from __future__ import annotations
from collections import defaultdict
from math import sqrt

def percentile(values: list[float], fraction: float) -> float | None:
    if not values: return None
    values = sorted(values)
    pos = (len(values) - 1) * fraction
    lower, upper = int(pos), min(int(pos) + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (pos - lower)

def statistics(values: list[float]) -> dict:
    if not values: return {"samples": 0}
    avg = sum(values) / len(values)
    return {"samples": len(values), "minimum_seconds": min(values), "p10_seconds": percentile(values, .1),
            "median_seconds": percentile(values, .5), "mean_seconds": avg, "p90_seconds": percentile(values, .9),
            "maximum_seconds": max(values), "standard_deviation_seconds": sqrt(sum((x-avg)**2 for x in values) / len(values)),
            "best_seconds": percentile(values, .1), "typical_seconds": percentile(values, .5), "worst_seconds": percentile(values, .9)}

def slot_label(departure_time: str) -> str:
    """Start of the 15-minute departure slot that contains `departure_time` (HH:MM)."""
    hour, minute = map(int, departure_time.split(":"))
    return f"{hour:02d}:{minute // 15 * 15:02d}"

def schedule_slots(schedules: dict) -> list[str]:
    """All 15-minute (or configured interval) slot starts inside the configured collection windows."""
    slots = set()
    for window in (schedules or {}).get("windows", []):
        (sh, sm), (eh, em) = (map(int, window[k].split(":")) for k in ("start", "end"))
        step = int(window.get("interval_minutes", 15))
        for minutes in range(sh * 60 + sm, eh * 60 + em, step):
            slots.add(slot_label(f"{minutes // 60}:{minutes % 60}"))
    return sorted(slots)

def grouped_statistics(measurements) -> dict:
    by_weekday, by_bucket, by_slot = defaultdict(list), defaultdict(list), defaultdict(lambda: defaultdict(list))
    values = []
    for row in measurements:
        values.append(row.duration_seconds); by_weekday[str(row.weekday)].append(row.duration_seconds)
        bucket = slot_label(row.departure_time); by_bucket[bucket].append(row.duration_seconds); by_slot[str(row.weekday)][bucket].append(row.duration_seconds)
    return {"overall": statistics(values), "by_weekday": {k: statistics(v) for k,v in by_weekday.items()}, "by_departure_bucket": {k: statistics(v) for k,v in by_bucket.items()},
            "by_weekday_slot": {day: {slot: statistics(v) for slot, v in slots.items()} for day, slots in by_slot.items()}}

LEVEL_CUTS = (0.02, 0.05, 0.10, 0.20)  # relative excess over the best slot that starts levels 2..5

def _slot_minutes(slot: str) -> int:
    hour, minute = map(int, slot.split(":"))
    return hour * 60 + minute

def _slot_label(minutes: int) -> str:
    return f"{minutes // 60 % 24:02d}:{minutes % 60:02d}"

def best_moments(by_weekday_slot: dict, min_samples: int = 1, min_spread: float = 0.03, z: float = 2.0) -> dict:
    """Rank every (weekday, 15-minute slot) of one journey from best to busiest, without exposing durations.

    * `level` (1 best .. 5 busiest) uses absolute thresholds on how much slower a slot is than the best one,
      so a 3% difference never looks as dramatic as a 30% one.
    * The best / busiest *windows* are the run of neighbouring slots on the same day that are practically as
      good (level 1) / as bad as the extreme slot, so a flat stretch is not decided by noise.
    * A ranking is only published when the busiest slot is at least `min_spread` slower than the best one and
      the gap exceeds `z` combined standard errors (the cells' own sampling noise).
    """
    cells = {(int(day), slot): stats for day, slots in by_weekday_slot.items()
             for slot, stats in slots.items() if stats["samples"] >= min_samples}
    means = {key: stats["mean_seconds"] for key, stats in cells.items()}
    result = {"meaningful": False, "spread": 0.0, "covered_cells": len(means), "best": None, "worst": None, "best_by_day": {}, "cells": {}}
    if len(means) < 2:
        return result
    best_key, worst_key = min(means, key=means.get), max(means, key=means.get)
    low, high = means[best_key], means[worst_key]
    spread = high / low - 1 if low > 0 else 0.0
    result["spread"] = round(spread, 4)

    errors = {key: stats["standard_deviation_seconds"] / stats["samples"] ** 0.5 for key, stats in cells.items()
              if stats["samples"] >= 2 and "standard_deviation_seconds" in stats}
    typical_error = sorted(errors.values())[len(errors) // 2] if errors else 0.0
    gap_noise = z * (errors.get(best_key, typical_error) ** 2 + errors.get(worst_key, typical_error) ** 2) ** 0.5
    if spread < min_spread or high - low <= gap_noise:
        return result

    level = lambda value: 1 + sum(value / low - 1 > cut for cut in LEVEL_CUTS)

    def window(key, qualifies):
        day, start = key[0], _slot_minutes(key[1])
        end = start
        while (day, _slot_label(start - 15)) in means and qualifies(means[(day, _slot_label(start - 15))]): start -= 15
        while (day, _slot_label(end + 15)) in means and qualifies(means[(day, _slot_label(end + 15))]): end += 15
        return {"day": day, "slot": _slot_label(start), "end": _slot_label(end + 15)}

    result.update(meaningful=True,
                  best=window(best_key, lambda v: level(v) == 1),
                  worst=window(worst_key, lambda v: v >= high / (1 + LEVEL_CUTS[0])))
    for (day, slot), value in means.items():
        result["cells"].setdefault(str(day), {})[slot] = {"level": level(value), "samples": cells[(day, slot)]["samples"]}
        if str(day) not in result["best_by_day"] or value < means[(day, result["best_by_day"][str(day)])]:
            result["best_by_day"][str(day)] = slot
    return result

def best_round_trips(outbound: dict, back: dict, work_hours=range(1, 13), min_spread: float = 0.03,
                     min_samples: int = 1, z: float = 2.0) -> dict:
    """Best moment to go A -> B, stay `work_hours` hours, and return B -> A, per length of stay.

    Both arguments are `by_weekday_slot` statistics (outbound = A->B, back = B->A). For every weekday and
    outbound slot, the return slot is the one in which you leave B: outbound slot + time on the road + stay.
    Trips are ranked by total time on the road, but only departure times are published, never durations.
    Same honesty rules as `best_moments`: a ranking needs `min_spread` and must exceed the sampling noise.
    """
    def cells(by_slot):
        return {(int(day), slot): stats for day, slots in by_slot.items()
                for slot, stats in slots.items() if stats["samples"] >= min_samples}
    def error(stats):
        return stats["standard_deviation_seconds"] / stats["samples"] ** 0.5 if stats["samples"] >= 2 and "standard_deviation_seconds" in stats else 0.0
    out_cells, back_cells = cells(outbound), cells(back)
    results = {}
    for hours in work_hours:
        trips = {}  # (day, outbound slot) -> (total seconds on the road, noise variance, return slot)
        for (day, slot), out in out_cells.items():
            leave_back = _slot_minutes(slot) + out["mean_seconds"] / 60 + hours * 60
            if leave_back >= 24 * 60:
                continue
            back_slot = _slot_label(int(leave_back // 15 * 15))
            if (day, back_slot) in back_cells:
                ret = back_cells[(day, back_slot)]
                trips[(day, slot)] = (out["mean_seconds"] + ret["mean_seconds"], error(out) ** 2 + error(ret) ** 2, back_slot)
        results[str(hours)] = _summarise_trips(trips, min_spread, z)
    return results

def _summarise_trips(trips: dict, min_spread: float, z: float) -> dict:
    if len(trips) < 2:
        return {"meaningful": False}
    best_key, worst_key = min(trips, key=lambda k: trips[k][0]), max(trips, key=lambda k: trips[k][0])
    low, high = trips[best_key][0], trips[worst_key][0]
    spread = high / low - 1
    if spread < min_spread or high - low <= z * (trips[best_key][1] + trips[worst_key][1]) ** 0.5:
        return {"meaningful": False, "spread": round(spread, 4)}

    def window(key, qualifies):
        day, start = key[0], _slot_minutes(key[1])
        end = start
        while (day, _slot_label(start - 15)) in trips and qualifies(trips[(day, _slot_label(start - 15))][0]): start -= 15
        while (day, _slot_label(end + 15)) in trips and qualifies(trips[(day, _slot_label(end + 15))][0]): end += 15
        return {"day": day, "out": {"slot": _slot_label(start), "end": _slot_label(end + 15)},
                "back": {"slot": trips[(day, _slot_label(start))][2], "end": _slot_label(_slot_minutes(trips[(day, _slot_label(end))][2]) + 15)}}

    tolerance = 1 + LEVEL_CUTS[0]
    days = {}
    for day in sorted({d for d, _ in trips}):
        day_best = min((k for k in trips if k[0] == day), key=lambda k: trips[k][0])
        day_low = trips[day_best][0]
        days[str(day)] = {**window(day_best, lambda total, day_low=day_low: total <= day_low * tolerance),
                          "level": 1 + sum(day_low / low - 1 > cut for cut in LEVEL_CUTS)}
    return {"meaningful": True, "spread": round(spread, 4),
            "best": window(best_key, lambda total: total <= low * tolerance),
            "worst": window(worst_key, lambda total: total >= high / tolerance), "days": days}
