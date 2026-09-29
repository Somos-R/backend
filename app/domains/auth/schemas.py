import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.passwords import Email, Password
from app.domains.users.schemas import UserDetailResponse

# ---------------------------------------------------------------------------
# Shared base — fields required for every actor
# ---------------------------------------------------------------------------

class _RegisterBase(BaseModel):
    email: Email
    password: Password
    full_name: str = Field(min_length=1, max_length=255)
    phone: str | None = Field(default=None, max_length=20)
    id_type: str = Field(max_length=10)
    id_number: str = Field(min_length=3, max_length=20)


# ---------------------------------------------------------------------------
# Per-actor registration schemas
# ---------------------------------------------------------------------------

class CitizenRegister(_RegisterBase):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{
                "user_type_code": "citizen",
                "email": "juan.perez@email.com",
                "password": "Segura12345",
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
    address: str | None = Field(default=None, max_length=500)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class BuildingRegister(_RegisterBase):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{
                "user_type_code": "building",
                "email": "admin@conjuntolaspalmas.com",
                "password": "Segura12345",
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
    building_name: str = Field(max_length=255)
    num_units: int = Field(ge=1, le=100000)
    representation_document: str | None = Field(default=None, max_length=2048)


class RecyclerRegister(_RegisterBase):
    """La asociación registra al reciclador sin contraseña; queda en estado pendiente (0)."""
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{
                "user_type_code": "recycler",
                "email": "carlos.recicla@email.com",
                "full_name": "Carlos Mendoza",
                "phone": "3156789012",
                "id_type": "CC",
                "id_number": "80234567",
            }]
        }
    )
    user_type_code: Literal["recycler"] = "recycler"
    password: Password | None = None  # type: ignore[assignment]  # no requerida; se asigna automáticamente al verificar


class EcaRegister(_RegisterBase):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{
                "user_type_code": "eca",
                "email": "operador@ecabogota.com",
                "password": "Segura12345",
                "full_name": "Luisa Ramírez",
                "phone": "3187654321",
                "id_type": "CC",
                "id_number": "30567890",
                "employee_code": "ECA-2024-015",
                "association_id": None,
                "role_code": "eca_operator",
            }]
        }
    )
    user_type_code: Literal["eca"] = "eca"
    employee_code: str | None = Field(default=None, max_length=50)
    association_id: uuid.UUID | None = None
    role_code: str | None = None


class AssociationRegister(_RegisterBase):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{
                "user_type_code": "association",
                "email": "admin@asobeum.org",
                "password": "Segura12345",
                "full_name": "Roberto Gómez",
                "phone": "3012345678",
                "id_type": "CC",
                "id_number": "79345678",
                "association_nit": "900123456-7",
                "legal_representative": "Roberto Gómez Vargas",
                "role_code": "association_admin",
            }]
        }
    )
    user_type_code: Literal["association"] = "association"
    association_nit: str = Field(max_length=50)
    legal_representative: str = Field(max_length=255)
    role_code: str | None = None


class B2bClientRegister(_RegisterBase):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{
                "user_type_code": "b2b_client",
                "email": "compras@industriasverdes.com",
                "password": "Segura12345",
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
    company_name: str = Field(max_length=255)
    tax_id: str = Field(max_length=50)
    commercial_contact: str | None = Field(default=None, max_length=255)


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
                "password": "Segura12345",
            }]
        }
    )

    email: str = Field(max_length=255)
    password: str = Field(max_length=128)


# ---------------------------------------------------------------------------
# One-time-token flows
# ---------------------------------------------------------------------------

class ActivateRequest(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    password: Password


class TokenRequest(BaseModel):
    token: str = Field(min_length=10, max_length=200)


class ForgotPasswordRequest(BaseModel):
    email: Email


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    password: Password


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(max_length=128)
    new_password: Password


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=10, max_length=200)


class MessageResponse(BaseModel):
    message: str


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


class MeResponse(UserDetailResponse):
    """The signed-in user's profile plus what they may do, evaluated by the server."""

    capabilities: list[str]


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # lifetime of the access token, in seconds
    refresh_token: str
