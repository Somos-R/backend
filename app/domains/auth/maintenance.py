"""Housekeeping for the token tables. Run on a schedule (see scripts/purge_expired_tokens.py)."""
from datetime import datetime, timedelta, timezone
from typing import cast

from sqlalchemy import CursorResult, Delete, delete, or_
from sqlalchemy.orm import Session

from app.domains.auth.models import OneTimeToken, RefreshToken, RevokedToken

# Keep spent rows a little while: useful when investigating an incident, and refresh-token
# rows are what makes reuse detection work until they expire.
GRACE = timedelta(days=7)


def _purge(db: Session, stmt: Delete) -> int:
    result = cast(CursorResult, db.execute(stmt.execution_options(synchronize_session=False)))
    return result.rowcount


def purge_expired(db: Session, now: datetime | None = None) -> dict[str, int]:
    """Delete rows that can no longer matter. Returns how many were removed per table.

    - revoked_tokens: once the JWT itself has expired it is rejected anyway.
    - one_time_tokens: expired or already used, past the grace period.
    - refresh_tokens: expired past the grace period (revoked/used ones are kept until then).
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - GRACE

    counts = {
        "revoked_tokens": _purge(db, delete(RevokedToken).where(RevokedToken.expires_at < now)),
        "one_time_tokens": _purge(db, delete(OneTimeToken).where(
            or_(OneTimeToken.expires_at < cutoff, OneTimeToken.used_at < cutoff))),
        "refresh_tokens": _purge(db, delete(RefreshToken).where(RefreshToken.expires_at < cutoff)),
    }
    db.commit()
    return counts
