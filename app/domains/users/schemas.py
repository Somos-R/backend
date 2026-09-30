import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.passwords import Email
from app.domains.users.enums import VerificationStatus


class UpdateUserRequest(BaseModel):
    """Todos los campos son opcionales — solo se actualizan los que se envíen."""

    # Common
    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    phone: str | None = Field(default=None, max_length=20)
    role_code: str | None = Field(default=None, max_length=20)

    # citizen / building
    address: str | None = Field(default=None, max_length=500)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

    # building
    building_name: str | None = Field(default=None, max_length=255)
    num_units: int | None = Field(default=None, ge=1, le=100000)
    representation_document: str | None = Field(default=None, max_length=2048)

    # recycler
    profile_picture: str | None = Field(default=None, max_length=2048)
    id_picture: str | None = Field(default=None, max_length=2048)

    # eca
    employee_code: str | None = Field(default=None, max_length=50)
    permissions: dict | None = None

    # association
    association_nit: str | None = Field(default=None, max_length=50)
    legal_representative: str | None = Field(default=None, max_length=255)

    # b2b_client
    company_name: str | None = Field(default=None, max_length=255)
    tax_id: str | None = Field(default=None, max_length=50)
    commercial_contact: str | None = Field(default=None, max_length=255)
    rep_goals: dict | None = None

    # recycler / eca / association
    association_id: uuid.UUID | None = None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "phone": "3001234567",
                    "role_code": "eca_admin",
                },
                {
                    "full_name": "Carlos Mendoza Ruiz",
                    "phone": "3156789012",
                    "profile_picture": "https://cdn.example.com/foto.jpg",
                },
            ]
        }
    )


class UserListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list["UserDetailResponse"]


class UserDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    phone: str | None
    id_type: str
    id_number: str
    user_type_code: str
    role_code: str | None
    is_active: bool
    email_verified_at: datetime | None
    # Invited and has not chosen a password yet: the moment to offer "resend invitation".
    pending_activation: bool
    created_at: datetime
    updated_at: datetime

    # citizen / building
    address: str | None
    latitude: float | None
    longitude: float | None

    # building
    building_name: str | None
    num_units: int | None
    representation_document: str | None

    # recycler
    profile_picture: str | None
    id_picture: str | None
    verification_status: VerificationStatus | None
    rejection_reason: str | None
    verified_at: datetime | None

    # eca
    employee_code: str | None
    permissions: dict | None

    # association
    association_nit: str | None
    legal_representative: str | None

    # b2b_client
    company_name: str | None
    tax_id: str | None
    commercial_contact: str | None
    rep_goals: dict | None

    # recycler / eca / association
    association_id: uuid.UUID | None

    # eca / association staff: the organization they work for (read-only: it is not editable)
    organization_id: uuid.UUID | None


class InviteStaffRequest(BaseModel):
    """Invite a person to the organization of whoever sends this. They choose their own password."""

    email: Email
    full_name: str = Field(min_length=2, max_length=255)
    id_type: str = Field(min_length=1, max_length=10)
    id_number: str = Field(min_length=3, max_length=20)
    phone: str | None = Field(default=None, max_length=20)
    role_code: str = Field(min_length=1, max_length=20)

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{
                "email": "carlos.mendoza@eca-norte.co",
                "full_name": "Carlos Mendoza Ruiz",
                "id_type": "CC",
                "id_number": "1020304050",
                "phone": "3156789012",
                "role_code": "eca_operator",
            }]
        }
    )


class UpdateRecyclerStatusRequest(BaseModel):
    status: VerificationStatus
    rejection_reason: str | None = None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"status": "verified"},
                {"status": "rejected", "rejection_reason": "Documentación de identidad incompleta"},
            ]
        }
    )

    @model_validator(mode="after")
    def require_reason_when_rejected(self):
        if self.status == VerificationStatus.rejected and not self.rejection_reason:
            raise ValueError("rejection_reason es requerido cuando el estado es rechazado (2)")
        return self
