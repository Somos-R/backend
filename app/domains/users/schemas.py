import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class UpdateUserRequest(BaseModel):
    """Todos los campos son opcionales — solo se actualizan los que se envíen."""

    # Common
    full_name: str | None = None
    phone: str | None = None
    role_code: str | None = None

    # citizen / building
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None

    # building
    building_name: str | None = None
    num_units: int | None = None
    representation_document: str | None = None

    # recycler
    profile_picture: str | None = None
    id_picture: str | None = None

    # eca
    employee_code: str | None = None
    permissions: dict | None = None

    # association
    association_nit: str | None = None
    legal_representative: str | None = None

    # b2b_client
    company_name: str | None = None
    tax_id: str | None = None
    commercial_contact: str | None = None
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
    verification_status: str | None

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
