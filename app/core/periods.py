"""The window of time a statistics endpoint covers."""
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Query

from app.core.errors import ApiError


@dataclass(frozen=True)
class Period:
    """`start` and `end` are both included; `end` is None when the period has no upper limit."""

    start: datetime
    end: datetime | None


def _as_utc(moment: datetime) -> datetime:
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment


def resolve_period(date_from: datetime | None, date_to: datetime | None) -> Period:
    """This month so far unless the client asks for another window.

    Both ends are included, like the filters of the weighings and transactions lists, so the same `date_to` gives the
    same rows in a list and in the figures above it. A naive date counts as UTC.
    """
    start = (_as_utc(date_from) if date_from is not None
             else datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0))
    end = _as_utc(date_to) if date_to is not None else None
    if end is not None and end < start:
        raise ApiError("invalid_period", status_code=422, detail="date_to no puede ser anterior a date_from")
    return Period(start, end)


def period_params(
    date_from: datetime | None = Query(
        default=None, description="Desde esta fecha y hora (incluida). Por defecto, el inicio del mes"),
    date_to: datetime | None = Query(
        default=None, description="Hasta esta fecha y hora (incluida, como en los listados). Por defecto, sin límite superior"),
) -> Period:
    """FastAPI dependency: the period of a stats endpoint, with its documentation in one place."""
    return resolve_period(date_from, date_to)
