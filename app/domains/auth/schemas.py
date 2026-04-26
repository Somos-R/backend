import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Shared base — fields required for every actor
# ---------------------------------------------------------------------------

class _RegisterBase(BaseModel):
    email: str
    password: str
    full_name: str
    phone: str | None = None
    id_type: str
    id_number: str


# ---------------------------------------------------------------------------
# Per-actor registration schemas
# ---------------------------------------------------------------------------

class CitizenRegister(_RegisterBase):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{
                "user_type_code": "citizen",
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
    user_type_code: Literal["citizen"] = "citizen"
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class BuildingRegister(_RegisterBase):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{
                "user_type_code": "building",
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
    user_type_code: Literal["building"] = "building"
    building_name: str
    num_units: int
    representation_document: str | None = None


class RecyclerRegister(_RegisterBase):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{
                "user_type_code": "recycler",
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
    user_type_code: Literal["recycler"] = "recycler"
    association_id: uuid.UUID | None = None


class EcaRegister(_RegisterBase):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{
                "user_type_code": "eca",
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
    user_type_code: Literal["eca"] = "eca"
    employee_code: str | None = None
    association_id: uuid.UUID | None = None


class AssociationRegister(_RegisterBase):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{
                "user_type_code": "association",
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
    user_type_code: Literal["association"] = "association"
    association_nit: str
    legal_representative: str


class B2bClientRegister(_RegisterBase):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{
                "user_type_code": "b2b_client",
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
    user_type_code: Literal["b2b_client"] = "b2b_client"
    company_name: str
    tax_id: str
    commercial_contact: str | None = None


# Discriminated union — Pydantic selects the right schema based on user_type_code
RegisterRequest = Annotated[
    CitizenRegister
    | BuildingRegister
    | RecyclerRegister
    | EcaRegister
    | AssociationRegister
    | B2bClientRegister,
    Field(discriminator="user_type_code"),
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
    user_type_code: str
    role_code: str | None
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
