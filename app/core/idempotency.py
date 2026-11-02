"""Idempotent creation: a client that retries a request with the same `Idempotency-Key` creates nothing new.

The mobile app works offline and resends what it created when the connection comes back; a retry after a lost
response must not record the same weighing or pickup request twice. The client generates a UUID when it
creates the record and sends it on every attempt. The server remembers, per account and operation, the key,
a fingerprint of the body and the response it gave:

- same key and same body: the original response is returned again and nothing is created;
- same key and a different body: `422 idempotency_key_reused` (the client mixed up two records);
- same key while the first attempt is still running: `409 idempotency_request_in_progress` (retry later).

The key is claimed in the same transaction that creates the record, so either both are saved or neither is:
a request that fails leaves the key free for the next attempt. Two simultaneous requests with the same key
meet at the unique index: the second waits for the first and then finds its key.
"""
import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import Header, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import DateTime, Index, SmallInteger, String, delete, func, select
from sqlalchemy.dialects.postgresql import JSONB, UUID, insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.core.database import Base
from app.core.errors import ApiError

HEADER = "Idempotency-Key"
# How long a key is remembered. An offline device that comes back later than this is a new request.
KEY_TTL = timedelta(days=7)

IdempotencyKeyHeader = Annotated[
    uuid.UUID | None,
    Header(alias=HEADER, description=(
        "Opcional. UUID generado por el cliente al crear el registro y repetido en cada reintento: la misma "
        "clave con el mismo cuerpo devuelve la respuesta original sin crear nada.")),
]


class IdempotencyKey(Base):
    """A key a client used for a creation, with the response it got; kept for `KEY_TTL`."""

    __tablename__ = "idempotency_keys"
    __table_args__ = (
        Index("uq_idempotency_keys_user_operation_key", "user_id", "operation", "key", unique=True),
        Index("ix_idempotency_keys_created_at", "created_at"),
    )

    id:               Mapped[uuid.UUID]   = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # No foreign key: this is a cache of responses, it must never keep an account from being removed.
    user_id:          Mapped[uuid.UUID]   = mapped_column(UUID(as_uuid=True), nullable=False)
    operation:        Mapped[str]         = mapped_column(String(64), nullable=False)
    key:              Mapped[uuid.UUID]   = mapped_column(UUID(as_uuid=True), nullable=False)
    request_hash:     Mapped[str]         = mapped_column(String(64), nullable=False)
    # Both empty while the first attempt is running.
    response_status:  Mapped[int | None]  = mapped_column(SmallInteger, nullable=True)
    response_body:    Mapped[Any | None]  = mapped_column(JSONB, nullable=True)
    created_at:       Mapped[datetime]    = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                          nullable=False)


def fingerprint(request: BaseModel) -> str:
    """SHA-256 of the validated body, independent of key order and of how the client formatted it."""
    canonical = json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _replay(row: IdempotencyKey, request_hash: str) -> JSONResponse:
    if row.request_hash != request_hash:
        raise ApiError("idempotency_key_reused", status_code=422,
                       detail="Esta Idempotency-Key ya se usó con otro contenido; genera una nueva para este registro")
    if row.response_status is None:
        raise ApiError("idempotency_request_in_progress", status_code=status.HTTP_409_CONFLICT,
                       detail="La petición original con esta Idempotency-Key aún se está procesando; reintenta en "
                              "unos segundos")
    return JSONResponse(content=row.response_body, status_code=row.response_status)


def _claim(
    db: Session, *, user_id: uuid.UUID, operation: str, key: uuid.UUID, request_hash: str,
) -> IdempotencyKey | JSONResponse:
    """The new key row (flushed, not committed), or the answer to give if the key is already known."""
    same_key = (IdempotencyKey.user_id == user_id, IdempotencyKey.operation == operation, IdempotencyKey.key == key)
    expired_before = datetime.now(timezone.utc) - KEY_TTL
    db.execute(delete(IdempotencyKey).where(*same_key, IdempotencyKey.created_at < expired_before))
    # A concurrent request holding the same key makes this INSERT wait until it commits or rolls back.
    claimed_id = db.scalar(
        insert(IdempotencyKey)
        .values(id=uuid.uuid4(), user_id=user_id, operation=operation, key=key, request_hash=request_hash)
        .on_conflict_do_nothing(index_elements=["user_id", "operation", "key"])
        .returning(IdempotencyKey.id))
    if claimed_id is None:
        existing = db.scalars(select(IdempotencyKey).where(*same_key)).one()
        return _replay(existing, request_hash)
    return db.get_one(IdempotencyKey, claimed_id)


def create_once(
    db: Session,
    *,
    user_id: uuid.UUID,
    key: uuid.UUID | None,
    operation: str,
    request: BaseModel,
    response_model: type[BaseModel],
    status_code: int,
    create: Callable[[], object],
) -> object:
    """Run `create` (a service call that commits) at most once per key; without a key, just run it.

    `operation` names the endpoint (e.g. "weighings.create"): the same key may be used for different kinds of
    record. The stored response is the serialized `response_model`, exactly what the first attempt returned.
    """
    if key is None:
        return create()
    request_hash = fingerprint(request)
    claimed = _claim(db, user_id=user_id, operation=operation, key=key, request_hash=request_hash)
    if isinstance(claimed, JSONResponse):
        return claimed
    try:
        created = create()
    except Exception:
        # Free the key: the record was not created, so the next attempt must be able to create it.
        db.rollback()
        raise
    body = response_model.model_validate(created).model_dump(mode="json", by_alias=True)
    claimed.response_status = status_code
    claimed.response_body = body
    db.commit()
    return JSONResponse(content=body, status_code=status_code)
