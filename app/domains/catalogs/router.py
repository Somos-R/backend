import uuid

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import search
from app.core.database import get_db
from app.core.permissions import PLATFORM_ROLES, ROLE_USER_TYPE
from app.core.rate_limit import limiter, register_limit
from app.domains.catalogs.docs import ASSOCIATIONS_DOCS, DOCUMENT_TYPES_DOCS, ROLES_DOCS
from app.domains.catalogs.models import DocumentType, Role
from app.domains.organizations.enums import OrganizationStatus, OrganizationType
from app.domains.organizations.models import Organization

router = APIRouter(prefix="/catalogs", tags=["catalogs"])


class DocumentTypeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    label: str


class RoleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    label: str
    user_type_code: str | None = None  # the actor type the role belongs to (eca, association)


@router.get("/document-types", response_model=list[DocumentTypeResponse], **DOCUMENT_TYPES_DOCS)
def get_document_types(db: Session = Depends(get_db)):
    return db.scalars(select(DocumentType).where(DocumentType.is_active.is_(True))).all()


class AssociationEntry(BaseModel):
    """What someone choosing their association needs to see: a name and a city."""

    id: uuid.UUID
    legal_name: str
    city: str | None


@router.get("/associations", response_model=list[AssociationEntry], **ASSOCIATIONS_DOCS)
@limiter.limit(register_limit)
def get_associations(
    request: Request,
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    query = select(Organization).where(
        Organization.type == OrganizationType.association, Organization.status == OrganizationStatus.approved)
    name_match = search.contains(q, [Organization.legal_name])
    if name_match is not None:
        query = query.where(name_match)
    return db.scalars(query.order_by(Organization.legal_name, Organization.id).offset(offset).limit(limit)).all()


@router.get("/roles", response_model=list[RoleResponse], **ROLES_DOCS)
def get_roles(db: Session = Depends(get_db)):
    # Somos R's own roles are not part of the public catalog: no client form assigns them.
    roles = db.scalars(select(Role).where(Role.is_active.is_(True), Role.code.not_in(PLATFORM_ROLES))).all()
    return [RoleResponse(code=r.code, label=r.label, user_type_code=ROLE_USER_TYPE.get(r.code)) for r in roles]
