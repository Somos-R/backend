from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.network import enforce_admin_network
from app.core.rate_limit import admin_limit, limiter
from app.core.security import require_capability
from app.domains.admin import catalogs_service as service
from app.domains.catalogs.models import DocumentType
from app.domains.inventory.models import Material
from app.domains.users.models import User

# Somos R's catalogs: needs the `catalogs.manage` capability, a backoffice token and an allowed network.
router = APIRouter(prefix="/admin/catalogs", tags=["admin"], dependencies=[Depends(enforce_admin_network)])
Manager = Depends(require_capability("catalogs.manage"))


class MaterialEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    label: str
    unit: str
    is_active: bool


class DocumentTypeEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    label: str
    is_active: bool


class CreateEntryRequest(BaseModel):
    code: str = Field(max_length=30, description="Inmutable. Materiales: minúsculas, números y _ (ej. `glass`). Tipos de documento: mayúsculas y números (ej. `PPT`)")
    label: str = Field(min_length=1, max_length=100)


class UpdateEntryRequest(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=100)
    is_active: bool | None = None

    @model_validator(mode="after")
    def _something_to_change(self):
        if self.label is None and self.is_active is None:
            raise ValueError("Indica label o is_active")
        return self


@router.get("/materials", response_model=list[MaterialEntry])
@limiter.limit(admin_limit)
def list_materials(
    request: Request, is_active: bool | None = Query(default=None), db: Session = Depends(get_db), _: User = Manager,
):
    """Every material, active or not (the public list only shows the active ones)."""
    return service.list_entries(db, Material, is_active)


@router.post("/materials", response_model=MaterialEntry, status_code=status.HTTP_201_CREATED)
@limiter.limit(admin_limit)
def create_material(request: Request, body: CreateEntryRequest, db: Session = Depends(get_db), actor: User = Manager):
    """A new material, in kg. Its `code` can never change."""
    return service.create_entry(db, actor, Material, "material", body.code, body.label)


@router.patch("/materials/{code}", response_model=MaterialEntry)
@limiter.limit(admin_limit)
def update_material(
    request: Request, code: str, body: UpdateEntryRequest, db: Session = Depends(get_db), actor: User = Manager,
):
    """Rename or (de)activate. A deactivated material is refused in new weighings and sales; what exists stays."""
    return service.update_entry(db, actor, Material, "material", code, body.label, body.is_active)


@router.get("/document-types", response_model=list[DocumentTypeEntry])
@limiter.limit(admin_limit)
def list_document_types(
    request: Request, is_active: bool | None = Query(default=None), db: Session = Depends(get_db), _: User = Manager,
):
    return service.list_entries(db, DocumentType, is_active)


@router.post("/document-types", response_model=DocumentTypeEntry, status_code=status.HTTP_201_CREATED)
@limiter.limit(admin_limit)
def create_document_type(
    request: Request, body: CreateEntryRequest, db: Session = Depends(get_db), actor: User = Manager,
):
    return service.create_entry(db, actor, DocumentType, "document_type", body.code, body.label)


@router.patch("/document-types/{code}", response_model=DocumentTypeEntry)
@limiter.limit(admin_limit)
def update_document_type(
    request: Request, code: str, body: UpdateEntryRequest, db: Session = Depends(get_db), actor: User = Manager,
):
    """A deactivated type is refused for new accounts and sellers; existing accounts keep theirs."""
    return service.update_entry(db, actor, DocumentType, "document_type", code, body.label, body.is_active)
