import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.domains.organizations.enums import OrganizationStatus, OrganizationType


class StartApplicationRequest(BaseModel):
    type: OrganizationType
    legal_name: str = Field(min_length=2, max_length=255)
    applicant_name: str = Field(min_length=2, max_length=255, description="Quien llena la solicitud")
    applicant_email: EmailStr = Field(description="A este correo llega el enlace para volver a la solicitud")
    tax_id: str | None = Field(default=None, max_length=50, description="NIT; puede completarse después")
    consent: bool = Field(description="Aceptación del tratamiento de datos (debe ser true)")

    @field_validator("consent")
    @classmethod
    def _must_consent(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Debes aceptar el tratamiento de datos para continuar")
        return value


class AccessLinkRequest(BaseModel):
    email: EmailStr


class UpdateApplicationRequest(BaseModel):
    legal_name: str | None = Field(default=None, min_length=2, max_length=255)
    tax_id: str | None = Field(default=None, max_length=50)
    legal_representative: str | None = Field(default=None, max_length=255)
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(default=None, max_length=20)
    address: str | None = Field(default=None, max_length=300)
    city: str | None = Field(default=None, max_length=100)
    applicant_name: str | None = Field(default=None, min_length=2, max_length=255)

    @model_validator(mode="after")
    def _something_to_change(self):
        if not self.model_fields_set:
            raise ValueError("Indica al menos un campo")
        return self


class ApplicationView(BaseModel):
    """What the applicant sees of their own request."""

    id: uuid.UUID
    type: OrganizationType
    status: OrganizationStatus
    legal_name: str
    tax_id: str | None
    legal_representative: str | None
    contact_email: str | None
    contact_phone: str | None
    address: str | None
    city: str | None
    applicant_name: str
    applicant_email: str
    consent_at: datetime
    submitted_at: datetime | None
    submission_count: int
    submissions_left: int
    can_edit: bool  # draft or changes requested
    can_submit: bool  # can_edit and nothing missing and sends left
    missing_fields: list[str]  # what is still empty and required to send


class ApplicationMessage(BaseModel):
    message: str
