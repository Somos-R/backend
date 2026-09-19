from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.core.database import get_db
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


@router.get("/document-types", response_model=list[DocumentTypeResponse], **DOCUMENT_TYPES_DOCS)
def get_document_types(db: Session = Depends(get_db)):
    return db.query(DocumentType).filter(DocumentType.is_active == True).all()


@router.get("/roles", response_model=list[RoleResponse], **ROLES_DOCS)
def get_roles(db: Session = Depends(get_db)):
    return db.query(Role).filter(Role.is_active == True).all()
