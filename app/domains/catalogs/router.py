from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.permissions import PLATFORM_ROLES, ROLE_USER_TYPE
from app.domains.catalogs.docs import DOCUMENT_TYPES_DOCS, ROLES_DOCS
from app.domains.catalogs.models import DocumentType, Role

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


@router.get("/roles", response_model=list[RoleResponse], **ROLES_DOCS)
def get_roles(db: Session = Depends(get_db)):
    # Somos R's own roles are not part of the public catalog: no client form assigns them.
    roles = db.scalars(select(Role).where(Role.is_active.is_(True), Role.code.not_in(PLATFORM_ROLES))).all()
    return [RoleResponse(code=r.code, label=r.label, user_type_code=ROLE_USER_TYPE.get(r.code)) for r in roles]
