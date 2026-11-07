import uuid

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.permissions import (
    ASSOC_ADMIN,
    ECA_ADMIN,
    ECA_NEARBY,
    ECA_ROUTING_PROFILE_READ,
    ECA_ROUTING_PROFILE_WRITE,
    ORG_ADMINS,
)
from app.core.rate_limit import limiter, register_limit
from app.core.security import require_roles
from app.domains.organizations import service
from app.domains.organizations.docs import (
    ACCEPT_LINK_DOCS,
    DIRECTORY_DOCS,
    GET_ECA_ROUTING_PROFILE_DOCS,
    LIST_LINKS_DOCS,
    LIST_NEARBY_ECAS_DOCS,
    REJECT_LINK_DOCS,
    REMOVE_LINK_DOCS,
    REQUEST_LINK_DOCS,
    UPDATE_ECA_ROUTING_PROFILE_DOCS,
)
from app.domains.organizations.enums import LinkStatus
from app.domains.organizations.schemas import (
    DirectoryResponse,
    EcaRoutingProfileRequest,
    EcaRoutingProfileResponse,
    LinkListResponse,
    LinkResponse,
    NearbyEcaListResponse,
    RejectLinkRequest,
    RequestLinkRequest,
)
from app.domains.users.models import User

router = APIRouter(tags=["links"])
ecas_router = APIRouter(prefix="/ecas", tags=["ecas"])


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


@ecas_router.get("/nearby", response_model=NearbyEcaListResponse, **LIST_NEARBY_ECAS_DOCS)
def nearby_ecas(
    lat: float = Query(ge=-90, le=90),
    lng: float = Query(ge=-180, le=180),
    radius: float = Query(default=5000, ge=1, le=50000),
    material: str | None = Query(default=None, min_length=1, max_length=30),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*ECA_NEARBY)),
):
    total, items = service.nearby_ecas(
        db, actor, latitude=lat, longitude=lng, radius_meters=radius, material=material, limit=limit, offset=offset)
    return NearbyEcaListResponse(total=total, items=items)


@ecas_router.get("/me/routing-profile", response_model=EcaRoutingProfileResponse, **GET_ECA_ROUTING_PROFILE_DOCS)
def get_routing_profile(
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*ECA_ROUTING_PROFILE_READ)),
):
    return service.get_routing_profile(db, actor)


@ecas_router.put("/me/routing-profile", response_model=EcaRoutingProfileResponse, **UPDATE_ECA_ROUTING_PROFILE_DOCS)
@limiter.limit(register_limit)
def update_routing_profile(
    request: Request,
    body: EcaRoutingProfileRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*ECA_ROUTING_PROFILE_WRITE)),
):
    return service.update_routing_profile(db, actor, body)
