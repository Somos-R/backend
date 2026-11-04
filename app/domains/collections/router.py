import uuid

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.idempotency import IdempotencyKeyHeader, create_once
from app.core.permissions import COLLECTIONS_REVIEW, forbidden
from app.core.rate_limit import limiter, register_limit
from app.core.security import get_current_user, require_roles
from app.domains.collections import service
from app.domains.collections.docs import (
    CANCEL_COLLECTION_DOCS,
    CREATE_COLLECTION_DOCS,
    GET_COLLECTION_DOCS,
    LIST_COLLECTIONS_DOCS,
    REJECT_COLLECTION_DOCS,
    REJECTION_REASONS_DOCS,
)
from app.domains.collections.models import CollectionStatus
from app.domains.collections.schemas import (
    CancelCollectionRequest,
    CollectionListResponse,
    CollectionResponse,
    CreateCollectionRequest,
    RejectCollectionRequest,
    RejectionReasonResponse,
)
from app.domains.users.models import User

router = APIRouter(prefix="/collections", tags=["collections"])


def get_requester(user: User = Depends(get_current_user)) -> User:
    """Who generates the material asks for it to be picked up: a citizen or a building."""
    if service.can_request(user):
        return user
    raise forbidden("Solo ciudadanos y edificios pueden solicitar recolección", "collections_requesters_only")


@router.post("", response_model=CollectionResponse, status_code=status.HTTP_201_CREATED, **CREATE_COLLECTION_DOCS)
@limiter.limit(register_limit)
def create_collection(
    request: Request,
    body: CreateCollectionRequest,
    idempotency_key: IdempotencyKeyHeader = None,
    db: Session = Depends(get_db),
    actor: User = Depends(get_requester),
):
    return create_once(
        db, user_id=actor.id, key=idempotency_key, operation="collections.create", request=body,
        response_model=CollectionResponse, status_code=status.HTTP_201_CREATED,
        create=lambda: service.create_collection(db, actor, body))


@router.get("", response_model=CollectionListResponse, **LIST_COLLECTIONS_DOCS)
def list_collections(
    status_: CollectionStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    actor: User = Depends(get_requester),
):
    total, items = service.list_collections(db, actor, status_=status_, limit=limit, offset=offset)
    return CollectionListResponse(total=total, items=items)


@router.get("/rejection-reasons", response_model=list[RejectionReasonResponse], **REJECTION_REASONS_DOCS)
def list_rejection_reasons(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return service.list_rejection_reasons(db)


@router.get("/{collection_id}", response_model=CollectionResponse, **GET_COLLECTION_DOCS)
def get_collection(
    collection_id: uuid.UUID,
    db: Session = Depends(get_db),
    actor: User = Depends(get_requester),
):
    return service.get_collection(db, actor, collection_id)


@router.patch("/{collection_id}/cancel", response_model=CollectionResponse, **CANCEL_COLLECTION_DOCS)
def cancel_collection(
    collection_id: uuid.UUID,
    body: CancelCollectionRequest | None = None,
    db: Session = Depends(get_db),
    actor: User = Depends(get_requester),
):
    return service.cancel_collection(db, actor, collection_id, body or CancelCollectionRequest())


@router.post("/{collection_id}/reject", response_model=CollectionResponse, **REJECT_COLLECTION_DOCS)
def reject_collection(
    collection_id: uuid.UUID,
    body: RejectCollectionRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*COLLECTIONS_REVIEW)),
):
    return service.reject_collection(db, actor, collection_id, body)
