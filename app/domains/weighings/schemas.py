import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domains.inventory.schemas import MaterialResponse, WarehouseResponse
from app.domains.weighings.models import AffiliationStatus, WeighingStatus


class RecyclerSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id:        uuid.UUID
    full_name: str
    id_number: str


class WeighingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:               uuid.UUID
    recycler_id:      uuid.UUID | None
    material_code:    str
    warehouse_id:     uuid.UUID
    kg:               Decimal
    price_per_kg:        Decimal
    status:           WeighingStatus
    rejection_reason: str | None
    validated_by:     uuid.UUID | None
    validated_at:     datetime | None
    occurred_at:            datetime
    created_at:       datetime
    total_value:      Decimal
    # How the seller relates to the ECA. Only `linked` weighings reach an association.
    affiliation_status: AffiliationStatus
    recycler:         RecyclerSummary | None
    # Someone who is not registered: identified by name and document.
    seller_name:      str | None
    seller_id_type:   str | None
    seller_id_number: str | None
    material:         MaterialResponse
    warehouse:        WarehouseResponse


class WeighingListResponse(BaseModel):
    total: int
    items: list[WeighingResponse]


class SellerInput(BaseModel):
    """The minimum to identify a person who sells material and is not registered in Somos R."""

    full_name: str = Field(min_length=2, max_length=255)
    id_type: str = Field(min_length=1, max_length=10)
    id_number: str = Field(min_length=3, max_length=20)


class CreateWeighingRequest(BaseModel):
    """Who delivers: a registered recycler (`recycler_id`) or a person identified by `seller`. Exactly one."""

    recycler_id:   uuid.UUID | None = None
    seller:        SellerInput | None = None
    material_code: str
    warehouse_id:  uuid.UUID
    kg:            Decimal
    price_per_kg:     Decimal

    @field_validator("kg", "price_per_kg")
    @classmethod
    def must_be_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("El valor debe ser mayor que cero")
        return v

    @model_validator(mode="after")
    def exactly_one_seller(self):
        if (self.recycler_id is None) == (self.seller is None):
            raise ValueError("Indica `recycler_id` (reciclador registrado) o `seller` (persona no registrada), no ambos ni ninguno")
        return self


class UpdateWeighingStatusRequest(BaseModel):
    status:           WeighingStatus
    rejection_reason: str | None = None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"status": "validated"},
                {"status": "rejected", "rejection_reason": "Peso incorrecto registrado"},
            ]
        }
    )


class WeighingStatsResponse(BaseModel):
    total_weighings_month: int
    total_kg_month:        Decimal
    pending_count:         int
    by_material:           list[dict]
