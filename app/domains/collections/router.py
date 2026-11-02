import uuid

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.permissions import forbidden
from app.core.rate_limit import limiter, register_limit
from app.core.security import get_current_user
from app.domains.collections import service
from app.domains.collections.docs import (
    CANCEL_COLLECTION_DOCS,
    CREATE_COLLECTION_DOCS,
    GET_COLLECTION_DOCS,
    LIST_COLLECTIONS_DOCS,
)
from app.domains.collections.models import CollectionStatus
from app.domains.collections.schemas import (
    CancelCollectionRequest,
    CollectionListResponse,
    CollectionResponse,
    CreateCollectionRequest,
)
from app.domains.users.models import User

router = APIRouter(prefix="/collections", tags=["collections"])

REQUESTER_TYPES = frozenset({"citizen", "building"})


def get_requester(user: User = Depends(get_current_user)) -> User:
    """Who generates the material asks for it to be picked up: a citizen or a building."""
    if user.user_type_code in REQUESTER_TYPES:
        return user
    raise forbidden("Solo ciudadanos y edificios pueden solicitar recolección", "collections_requesters_only")


@router.post("", response_model=CollectionResponse, status_code=status.HTTP_201_CREATED, **CREATE_COLLECTION_DOCS)
@limiter.limit(register_limit)
def create_collection(
    request: Request,
    body: CreateCollectionRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(get_requester),
):
    return service.create_collection(db, actor, body)


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
