"""Shared pagination for list endpoints (SQLAlchemy 2.0 `select()` style)."""
from collections.abc import Sequence
from typing import Any, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

T = TypeVar("T")


def paginate(
    db: Session,
    stmt: Select[tuple[T]],
    *order_by: Any,
    limit: int,
    offset: int,
    options: Sequence[Any] = (),
) -> tuple[int, list[T]]:
    """Return (total matching rows, one page of them).

    `order_by` must end with a unique column (normally `id`) so pages never overlap or skip rows;
    `options` carries the `selectinload(...)` of the relations the response includes.
    """
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    page = db.scalars(stmt.options(*options).order_by(*order_by).offset(offset).limit(limit)).all()
    return total, list(page)
