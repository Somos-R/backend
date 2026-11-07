"""Batch upload of what a device saved offline.

Each operation is processed on its own, through the same creation as the endpoint that serves it and with the same
idempotency (`app/core/idempotency.py`): one that fails is reported and leaves the others untouched, and sending the
batch again creates nothing twice. This domain only orchestrates: the rules of a weighing or of a pickup request stay
in their own domains.
"""
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import status
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.core.idempotency import create_once
from app.core.permissions import WEIGHINGS_CREATE, forbidden, has_role
from app.domains.collections import service as collections_service
from app.domains.collections.schemas import CollectionResponse, CreateCollectionRequest
from app.domains.sync.schemas import (
    ItemStatus,
    SyncBatchRequest,
    SyncBatchResponse,
    SyncItemError,
    SyncItemResult,
    SyncOperation,
    SyncSummary,
)
from app.domains.users.models import User
from app.domains.weighings import service as weighings_service
from app.domains.weighings.schemas import CreateWeighingRequest, WeighingResponse

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Handler:
    """How one kind of record is created: the same operation name, body and service as its own endpoint."""

    operation: str  # shared with the endpoint, so a key used in one place is known in the other
    request_model: type[BaseModel]
    response_model: type[BaseModel]
    ensure_allowed: Callable[[User], None]
    create: Callable[[Session, User, Any], object]


def _ensure_may_weigh(actor: User) -> None:
    if not has_role(actor, WEIGHINGS_CREATE):
        raise forbidden()


def _ensure_may_request_pickup(actor: User) -> None:
    if not collections_service.can_request(actor):
        raise forbidden("Solo ciudadanos y edificios pueden solicitar recolección", "collections_requesters_only")


_HANDLERS = {
    "weighing": _Handler("weighings.create", CreateWeighingRequest, WeighingResponse, _ensure_may_weigh,
                         weighings_service.create_weighing),
    "collection": _Handler("collections.create", CreateCollectionRequest, CollectionResponse,
                           _ensure_may_request_pickup, collections_service.create_collection),
}


def _failed(index: int, operation: SyncOperation, code: str, detail: Any) -> SyncItemResult:
    return SyncItemResult(
        index=index, type=operation.type, idempotency_key=operation.idempotency_key,
        client_created_at=operation.client_created_at, status="error", record=None,
        error=SyncItemError(code=code, detail=detail))


def _process(db: Session, actor: User, index: int, operation: SyncOperation) -> SyncItemResult:
    handler = _HANDLERS[operation.type]
    try:
        handler.ensure_allowed(actor)
        body = handler.request_model.model_validate(operation.payload)
    except ApiError as exc:
        return _failed(index, operation, exc.code, exc.detail)
    except ValidationError as exc:
        return _failed(index, operation, "validation_error", json.loads(exc.json(include_url=False)))

    created_here = False

    def create() -> object:
        nonlocal created_here
        created_here = True
        return handler.create(db, actor, body)

    try:
        answer = create_once(
            db, user_id=actor.id, key=operation.idempotency_key, operation=handler.operation, request=body,
            response_model=handler.response_model, status_code=status.HTTP_201_CREATED, create=create)
    except ApiError as exc:
        db.rollback()
        return _failed(index, operation, exc.code, exc.detail)
    except Exception:
        # One bad record must not stop the rest, but it must not vanish either: it is logged with its traceback.
        db.rollback()
        logger.exception("sync batch: operation %s (%s) failed", index, operation.type)
        return _failed(index, operation, "internal_error", "No se pudo procesar esta operación")

    status_: ItemStatus = "created" if created_here else "duplicate"
    return SyncItemResult(
        index=index, type=operation.type, idempotency_key=operation.idempotency_key,
        client_created_at=operation.client_created_at, status=status_,
        record=json.loads(bytes(answer.body)), error=None)  # type: ignore[attr-defined]


def process_batch(db: Session, actor: User, request: SyncBatchRequest) -> SyncBatchResponse:
    """Process the operations oldest first (by when the device saved them) and answer in the order they came."""
    by_age = sorted(enumerate(request.operations), key=lambda pair: pair[1].client_created_at)
    results = {index: _process(db, actor, index, operation) for index, operation in by_age}
    ordered = [results[index] for index in range(len(request.operations))]
    return SyncBatchResponse(
        summary=SyncSummary(
            created=sum(r.status == "created" for r in ordered),
            duplicate=sum(r.status == "duplicate" for r in ordered),
            error=sum(r.status == "error" for r in ordered)),
        results=ordered)
