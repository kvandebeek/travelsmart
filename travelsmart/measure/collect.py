"""Turn one chain request into validated per-edge rows (docs/network-design.md §6, §7).

A chain's legs come back in the order its waypoints were asked for, so leg *i* is the chain's edge
*i*. Each leg is checked against that edge before it is stored: a provider free to route around a jam
otherwise files another road's time under this edge.

A leg is stored for the moment it was actually driven. On a chain the later legs are reached minutes
after the request, and TomTom reports each leg's own departureTime, so that is what decides the
weekday and half hour a measurement belongs to.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from travelsmart.db import LegMeasurement
from travelsmart.measure.validation import accepts

LOCAL = ZoneInfo("Europe/Brussels")


@dataclass
class Stored:
    accepted: int = 0
    rejected: int = 0

    @property
    def total(self) -> int:
        return self.accepted + self.rejected


def half_hour_of(moment: datetime) -> tuple[int, int]:
    """The local weekday (0 = Monday) and half hour of the day (0-47) a measurement belongs to."""
    local = moment.astimezone(LOCAL)
    return local.weekday(), local.hour * 2 + (1 if local.minute >= 30 else 0)


def rows_for_chain(chain: dict, legs, edges: dict[str, dict], provider: str,
                   requested_at: datetime | None = None) -> list[LegMeasurement]:
    """Match a provider's legs to the chain's edges and build one row each, accepted or not.

    Extra legs a provider returns beyond the chain's edges are ignored: the chain defines what was
    asked for, and anything past it cannot be attributed to an edge.
    """
    requested_at = requested_at or datetime.now(timezone.utc)
    created = datetime.now(timezone.utc)
    rows = []
    for leg, edge_id in zip(legs, chain["edges"]):
        edge = edges[edge_id]
        moment = getattr(leg, "departure", None) or requested_at
        weekday, half_hour = half_hour_of(moment)
        ok, why = accepts(edge, leg.metres)
        rows.append(LegMeasurement(
            edge_id=edge_id, provider=provider, chain_id=chain["id"],
            departure_utc=moment.astimezone(timezone.utc), weekday=weekday, half_hour=half_hour,
            seconds=leg.seconds, free_flow_seconds=leg.free_flow_seconds,
            typical_seconds=leg.typical_seconds, metres=leg.metres,
            roads=",".join(edge.get("roads", [])) or None,
            accepted=ok, rejected_because=why or None, created_at=created,
        ))
    return rows


def store(session, rows: list[LegMeasurement]) -> Stored:
    """Write a chain's rows in one commit, so a whole chain lands or none of it does."""
    session.add_all(rows)
    session.commit()
    return Stored(accepted=sum(1 for row in rows if row.accepted),
                  rejected=sum(1 for row in rows if not row.accepted))
