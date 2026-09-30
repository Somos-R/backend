import uuid

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.permissions import ASSOC_ADMIN, ECA_ADMIN, ORG_ADMINS
from app.core.rate_limit import limiter, register_limit
from app.core.security import require_roles
from app.domains.organizations import service
from app.domains.organizations.docs import (
    ACCEPT_LINK_DOCS,
    DIRECTORY_DOCS,
    LIST_LINKS_DOCS,
    REJECT_LINK_DOCS,
    REMOVE_LINK_DOCS,
    REQUEST_LINK_DOCS,
)
from app.domains.organizations.enums import LinkStatus
from app.domains.organizations.schemas import (
    DirectoryResponse,
    LinkListResponse,
    LinkResponse,
    RejectLinkRequest,
    RequestLinkRequest,
)
from app.domains.users.models import User

router = APIRouter(tags=["links"])


@router.get("/directory/associations", response_model=DirectoryResponse, **DIRECTORY_DOCS)
def associations_directory(
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(ECA_ADMIN)),
):
    total, items = service.directory(db, actor, q, limit, offset)
    return DirectoryResponse(total=total, limit=limit, offset=offset, items=items)


@router.post("/links", response_model=LinkResponse, status_code=status.HTTP_201_CREATED, **REQUEST_LINK_DOCS)
@limiter.limit(register_limit)
def request_link(
    request: Request,
    body: RequestLinkRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(ECA_ADMIN)),
):
    return service.request_link(db, actor, body.association_id)


@router.get("/links", response_model=LinkListResponse, **LIST_LINKS_DOCS)
def list_links(
    status_: LinkStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*ORG_ADMINS)),
):
    total, items = service.list_links(db, actor, status_, limit, offset)
    return LinkListResponse(total=total, limit=limit, offset=offset, items=items)


@router.post("/links/{link_id}/accept", response_model=LinkResponse, **ACCEPT_LINK_DOCS)
@limiter.limit(register_limit)
def accept_link(
    request: Request,
    link_id: uuid.UUID,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(ASSOC_ADMIN)),
):
    return service.decide(db, actor, link_id, True, None)


@router.post("/links/{link_id}/reject", response_model=LinkResponse, **REJECT_LINK_DOCS)
@limiter.limit(register_limit)
def reject_link(
    request: Request,
    link_id: uuid.UUID,
    body: RejectLinkRequest | None = None,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(ASSOC_ADMIN)),
):
    return service.decide(db, actor, link_id, False, body.reason if body else None)


@router.post("/links/{link_id}/remove", response_model=LinkResponse, **REMOVE_LINK_DOCS)
@limiter.limit(register_limit)
def remove_link(
    request: Request,
    link_id: uuid.UUID,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*ORG_ADMINS)),
):
    return service.remove(db, actor, link_id)
