import uuid
from datetime import datetime, time
from decimal import Decimal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from app.domains.inventory.schemas import unique_material_codes
from app.domains.organizations.enums import LinkStatus, Weekday

MAX_RECEPTION_WINDOWS = 21  # three windows a day at most
MAX_ACCEPTED_MATERIALS = 20


class OrganizationRef(BaseModel):
    """What one side of a link may know about the other: a name and a city, nothing more."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    legal_name: str
    city: str | None


class DirectoryEntry(OrganizationRef):
    link_status: LinkStatus | None  # the state of your link with it, if you have one


class DirectoryResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[DirectoryEntry]


class RequestLinkRequest(BaseModel):
    association_id: uuid.UUID


class RejectLinkRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=200)


class LinkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: LinkStatus
    eca: OrganizationRef
    association: OrganizationRef
    rejection_reason: str | None
    decided_at: datetime | None
    created_at: datetime
    updated_at: datetime


class LinkListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[LinkResponse]


class ReceptionWindow(BaseModel):
    """One stretch of a day in which the ECA receives material. A day may have several (e.g. a lunch break)."""

    day: Weekday
    opens_at: time
    closes_at: time

    @field_validator("opens_at", "closes_at")
    @classmethod
    def whole_minutes(cls, value: time) -> time:
        if value.second or value.microsecond or value.tzinfo is not None:
            raise ValueError("Las horas van en formato HH:MM, sin segundos ni zona horaria")
        return value

    @model_validator(mode="after")
    def opens_before_it_closes(self) -> "ReceptionWindow":
        if self.opens_at >= self.closes_at:
            raise ValueError("La hora de apertura debe ser anterior a la de cierre")
        return self

    @field_serializer("opens_at", "closes_at")
    def as_hours_and_minutes(self, value: time) -> str:
        return value.strftime("%H:%M")


class EcaRoutingProfileRequest(BaseModel):
    """Everything route planning needs from an ECA. PUT replaces the whole profile."""

    latitude: Decimal = Field(ge=-90, le=90, max_digits=9, decimal_places=6)
    longitude: Decimal = Field(ge=-180, le=180, max_digits=9, decimal_places=6)
    reception_hours: list[ReceptionWindow] = Field(min_length=1, max_length=MAX_RECEPTION_WINDOWS)
    accepted_materials: list[str] = Field(min_length=1, max_length=MAX_ACCEPTED_MATERIALS)

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{
                "latitude": 4.5981,
                "longitude": -74.0758,
                "reception_hours": [
                    {"day": "monday", "opens_at": "08:00", "closes_at": "12:00"},
                    {"day": "monday", "opens_at": "14:00", "closes_at": "17:00"},
                    {"day": "saturday", "opens_at": "08:00", "closes_at": "12:00"},
                ],
                "accepted_materials": ["paper", "plastic"],
            }]
        }
    )

    unique_codes = field_validator("accepted_materials")(unique_material_codes)

    @field_validator("reception_hours")
    @classmethod
    def no_overlaps(cls, windows: list[ReceptionWindow]) -> list[ReceptionWindow]:
        """Sorted by day and opening time; two windows of the same day may touch but not overlap."""
        days = list(Weekday)
        ordered = sorted(windows, key=lambda w: (days.index(w.day), w.opens_at))
        for previous, current in zip(ordered, ordered[1:]):
            if previous.day == current.day and current.opens_at < previous.closes_at:
                raise ValueError("Los horarios de recepción de un mismo día no pueden cruzarse")
        return ordered


class EcaRoutingProfileResponse(BaseModel):
    """The ECA's routing data; each part is null until the ECA declares it."""

    organization_id: uuid.UUID
    latitude: Decimal | None
    longitude: Decimal | None
    reception_hours: list[ReceptionWindow] | None
    accepted_materials: list[str] | None
    updated_at: datetime
