import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_validator

from app.domains.weighings.models import WeighingStatus
from app.domains.inventory.schemas import MaterialResponse, WarehouseResponse


class RecyclerSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id:        uuid.UUID
    full_name: str
    id_number: str


class WeighingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:               uuid.UUID
    recycler_id:      uuid.UUID
    material_code:    str
    warehouse_id:     uuid.UUID
    kg:               Decimal
    precio_kg:        Decimal
    estado:           WeighingStatus
    rejection_reason: str | None
    validated_by:     uuid.UUID | None
    validated_at:     datetime | None
    fecha:            datetime
    created_at:       datetime
    total_value:      Decimal
    recycler:         RecyclerSummary
    material:         MaterialResponse
    warehouse:        WarehouseResponse


class WeighingListResponse(BaseModel):
    total: int
    items: list[WeighingResponse]


class CreateWeighingRequest(BaseModel):
    recycler_id:   uuid.UUID
    material_code: str
    warehouse_id:  uuid.UUID
    kg:            Decimal
    precio_kg:     Decimal

    @field_validator("kg", "precio_kg")
    @classmethod
    def must_be_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("El valor debe ser mayor que cero")
        return v


class UpdateWeighingStatusRequest(BaseModel):
    status:           WeighingStatus
    rejection_reason: str | None = None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"status": "validado"},
                {"status": "rechazado", "rejection_reason": "Peso incorrecto registrado"},
            ]
        }
    )


class WeighingStatsResponse(BaseModel):
    total_weighings_month: int
    total_kg_month:        Decimal
    pending_count:         int
    by_material:           list[dict]
