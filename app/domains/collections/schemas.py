import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domains.collections.models import CollectionStatus

MAX_MATERIALS = 10


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
    created_at:          datetime
    updated_at:          datetime


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

    @field_validator("materials")
    @classmethod
    def unique_codes(cls, codes: list[str]) -> list[str]:
        """Each material once, in the order given; a code never longer than the catalog allows."""
        if any(not code or len(code) > 30 for code in codes):
            raise ValueError("Cada material debe ser un código de 1 a 30 caracteres")
        return list(dict.fromkeys(codes))


class CancelCollectionRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=200)
