"""The window of time a statistics endpoint covers."""
from datetime import datetime, timezone

from app.core.errors import ApiError


def _as_utc(moment: datetime) -> datetime:
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment


def resolve_period(date_from: datetime | None, date_to: datetime | None) -> tuple[datetime, datetime | None]:
    """`[start, end)` for the stats: this month so far unless the client asks for another window.

    `date_from` is included and `date_to` excluded, like the ledger and the exports. A naive date counts as UTC.
    """
    start = (_as_utc(date_from) if date_from is not None
             else datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0))
    end = _as_utc(date_to) if date_to is not None else None
    if end is not None and end <= start:
        raise ApiError("invalid_period", status_code=422, detail="date_to debe ser posterior a date_from")
    return start, end
