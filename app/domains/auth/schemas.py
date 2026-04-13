import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domains.users.enums import UserType


# ---------------------------------------------------------------------------
# Shared base — fields required for every actor
# ---------------------------------------------------------------------------

class _RegisterBase(BaseModel):
    email: str
    password: str
    full_name: str
    phone: str | None = None
    id_type: str   # code from document_types table, e.g. "CC", "CE", "NIT"
    id_number: str


# ---------------------------------------------------------------------------
# Per-actor registration schemas
# ---------------------------------------------------------------------------

class CitizenRegister(_RegisterBase):
    user_type: Literal[UserType.citizen] = UserType.citizen
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class BuildingAdminRegister(_RegisterBase):
    user_type: Literal[UserType.building_admin] = UserType.building_admin
    building_name: str
    num_units: int
    representation_document: str | None = None


class RecyclerRegister(_RegisterBase):
    user_type: Literal[UserType.recycler] = UserType.recycler
    association_id: uuid.UUID | None = None


class EcaOperatorRegister(_RegisterBase):
    user_type: Literal[UserType.eca_operator] = UserType.eca_operator
    employee_code: str | None = None
    association_id: uuid.UUID | None = None


class AsobeumAdminRegister(_RegisterBase):
    user_type: Literal[UserType.asobeum_admin] = UserType.asobeum_admin
    association_nit: str
    legal_representative: str


class B2bClientRegister(_RegisterBase):
    user_type: Literal[UserType.b2b_client] = UserType.b2b_client
    company_name: str
    tax_id: str
    commercial_contact: str | None = None


# Discriminated union — Pydantic selects the right schema based on user_type
RegisterRequest = Annotated[
    CitizenRegister
    | BuildingAdminRegister
    | RecyclerRegister
    | EcaOperatorRegister
    | AsobeumAdminRegister
    | B2bClientRegister,
    Field(discriminator="user_type"),
]


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    email: str
    password: str


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------

class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    phone: str | None
    id_type: str
    id_number: str
    user_type: UserType
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
