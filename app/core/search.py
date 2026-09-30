"""Text search shared by every list that has a `q` filter.

"Contains", ignoring case and accents (needs the `unaccent` extension). The characters that mean something
to LIKE (`%`, `_` and the backslash) are escaped: they are text to find, never wildcards.
"""
from collections.abc import Iterable
from typing import Any

from sqlalchemy import ColumnElement, func, or_

MIN_SEARCH_LENGTH = 2


def like_pattern(q: str | None) -> str | None:
    """`%text%` ready for an escaped ILIKE, or None when `q` is empty or shorter than the minimum."""
    text = (q or "").strip()
    if len(text) < MIN_SEARCH_LENGTH:
        return None
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def contains(q: str | None, columns: Iterable[Any]) -> ColumnElement[bool] | None:
    """A condition that is true when any of `columns` contains `q`; None when `q` is not usable."""
    pattern = like_pattern(q)
    if pattern is None:
        return None
    wanted = func.unaccent(pattern)
    return or_(*(func.unaccent(column).ilike(wanted, escape="\\") for column in columns))
