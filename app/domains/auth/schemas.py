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
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{
                "user_type": "citizen",
                "email": "juan.perez@email.com",
                "password": "segura123",
                "full_name": "Juan Pérez",
                "phone": "3001234567",
                "id_type": "CC",
                "id_number": "1023456789",
                "address": "Calle 45 # 12-34, Bogotá",
                "latitude": 4.6097,
                "longitude": -74.0817,
            }]
        }
    )
    user_type: Literal[UserType.citizen] = UserType.citizen
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class BuildingAdminRegister(_RegisterBase):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{
                "user_type": "building_admin",
                "email": "admin@conjuntolaspalmas.com",
                "password": "segura123",
                "full_name": "María Torres",
                "phone": "3109876543",
                "id_type": "CC",
                "id_number": "52456789",
                "building_name": "Conjunto Las Palmas",
                "num_units": 48,
                "representation_document": None,
            }]
        }
    )
    user_type: Literal[UserType.building_admin] = UserType.building_admin
    building_name: str
    num_units: int
    representation_document: str | None = None


class RecyclerRegister(_RegisterBase):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{
                "user_type": "recycler",
                "email": "carlos.recicla@email.com",
                "password": "segura123",
                "full_name": "Carlos Mendoza",
                "phone": "3156789012",
                "id_type": "CC",
                "id_number": "80234567",
                "association_id": None,
            }]
        }
    )
    user_type: Literal[UserType.recycler] = UserType.recycler
    association_id: uuid.UUID | None = None


class EcaOperatorRegister(_RegisterBase):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{
                "user_type": "eca_operator",
                "email": "operador@ecabogota.com",
                "password": "segura123",
                "full_name": "Luisa Ramírez",
                "phone": "3187654321",
                "id_type": "CC",
                "id_number": "30567890",
                "employee_code": "ECA-2024-015",
                "association_id": None,
            }]
        }
    )
    user_type: Literal[UserType.eca_operator] = UserType.eca_operator
    employee_code: str | None = None
    association_id: uuid.UUID | None = None


class AsobeumAdminRegister(_RegisterBase):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{
                "user_type": "asobeum_admin",
                "email": "admin@asobeum.org",
                "password": "segura123",
                "full_name": "Roberto Gómez",
                "phone": "3012345678",
                "id_type": "CC",
                "id_number": "79345678",
                "association_nit": "900123456-7",
                "legal_representative": "Roberto Gómez Vargas",
            }]
        }
    )
    user_type: Literal[UserType.asobeum_admin] = UserType.asobeum_admin
    association_nit: str
    legal_representative: str


class B2bClientRegister(_RegisterBase):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{
                "user_type": "b2b_client",
                "email": "compras@industriasverdes.com",
                "password": "segura123",
                "full_name": "Andrés Castillo",
                "phone": "3223456789",
                "id_type": "CC",
                "id_number": "1098765432",
                "company_name": "Industrias Verdes S.A.S.",
                "tax_id": "901234567-8",
                "commercial_contact": "Andrés Castillo - Jefe de Compras",
            }]
        }
    )
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
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{
                "email": "juan.perez@email.com",
                "password": "segura123",
            }]
        }
    )

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
