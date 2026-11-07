import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domains.collections.models import CollectionStatus
from app.domains.inventory.schemas import unique_material_codes

MAX_MATERIALS = 10


class RejectionReasonResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code:           str
    label:          str
    is_correctable: bool


class CollectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:                  uuid.UUID
    requester_id:        uuid.UUID
    status:              CollectionStatus
    address:             str
    latitude:            Decimal
    longitude:           Decimal
    materials:           list[str]
    notes:               str | None
    scheduled_date:      date | None
    cancellation_reason: str | None
    cancelled_at:        datetime | None
    association_id:      uuid.UUID | None
    rejection_reason:    RejectionReasonResponse | None
    rejected_at:         datetime | None
    client_created_at:   datetime | None  # when the device saved it, for what came through POST /sync/batch
    created_at:          datetime
    updated_at:          datetime


class NearbyCollectionResponse(CollectionResponse):
    distance_meters: float  # straight line from the point asked for, on the spheroid


class NearbyCollectionListResponse(BaseModel):
    total: int  # all inside the radius, not just this page
    items: list[NearbyCollectionResponse]


class CollectionListResponse(BaseModel):
    total: int
    items: list[CollectionResponse]


class CreateCollectionRequest(BaseModel):
    """Who asks is always the signed-in account: the client never says it."""

    address:        str = Field(min_length=5, max_length=255)
    latitude:       Decimal = Field(ge=-90, le=90, max_digits=9, decimal_places=6)
    longitude:      Decimal = Field(ge=-180, le=180, max_digits=9, decimal_places=6)
    materials:      list[str] = Field(min_length=1, max_length=MAX_MATERIALS)
    notes:          str | None = Field(default=None, max_length=500)
    scheduled_date: date | None = None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{
                "address": "Calle 45 # 12-30, Bogotá",
                "latitude": 4.636,
                "longitude": -74.065,
                "materials": ["paper", "plastic"],
                "notes": "Portería, preguntar por la administración",
                "scheduled_date": "2026-11-10",
            }]
        }
    )

    unique_codes = field_validator("materials")(unique_material_codes)


class CancelCollectionRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=200)


class RejectCollectionRequest(BaseModel):
    """A code of the `rejection_reasons` catalog: never free text."""

    reason_code: str = Field(min_length=1, max_length=40)

    model_config = ConfigDict(json_schema_extra={"examples": [{"reason_code": "incomplete_address"}]})
