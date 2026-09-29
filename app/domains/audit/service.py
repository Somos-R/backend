"""Writing to the audit trail.

`record` adds the row to the caller's session, so it commits or rolls back **together with the
action it describes**: no phantom entries for operations that failed, and none missing for
operations that succeeded. For events that must survive the request failing (a rejected login),
the caller commits explicitly before raising.
"""
import json
from typing import Any

from sqlalchemy.orm import Session

from app.core.context import get_client_ip, get_request_id
from app.domains.audit.actions import SUCCESS
from app.domains.audit.models import AuditLog

# Detail keys containing any of these are masked: a secret must never reach the trail, even by mistake.
_SENSITIVE = ("password", "token", "secret", "hash", "authorization", "cookie", "api_key")
REDACTED = "[redacted]"
MAX_STRING = 200
MAX_DETAILS_BYTES = 4000


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(k): REDACTED if any(s in str(k).lower() for s in _SENSITIVE) else _clean(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_clean(v) for v in value]
    if isinstance(value, str):
        return value if len(value) <= MAX_STRING else value[:MAX_STRING] + "…"
    return value


def sanitize(details: dict | None) -> dict:
    """JSON-safe, size-bounded, secrets masked."""
    if not details:
        return {}
    cleaned = json.loads(json.dumps(_clean(details), default=str))
    if len(json.dumps(cleaned)) > MAX_DETAILS_BYTES:
        return {"truncated": True}
    return cleaned


def record(
    db: Session,
    action: str,
    *,
    actor=None,
    target_type: str | None = None,
    target_id: Any = None,
    outcome: str = SUCCESS,
    details: dict | None = None,
) -> AuditLog:
    """Add an audit row to `db`. The caller commits."""
    entry = AuditLog(
        action=action,
        outcome=outcome,
        actor_id=getattr(actor, "id", None),
        actor_role=(getattr(actor, "role_code", None) or getattr(actor, "user_type_code", None)),
        target_type=target_type,
        target_id=str(target_id)[:64] if target_id is not None else None,
        ip=(get_client_ip() or None),
        request_id=get_request_id(),
        details=sanitize(details),
    )
    db.add(entry)
    return entry
