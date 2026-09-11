import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_validator

from app.domains.transactions.models import TransactionStatus, TransactionType
from app.domains.inventory.schemas import MaterialResponse, WarehouseResponse


class RecyclerSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id:        uuid.UUID
    full_name: str
    id_number: str


class TransactionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:            uuid.UUID
    type:          TransactionType
    status:        TransactionStatus
    material_code: str
    warehouse_id:  uuid.UUID
    kg:            Decimal
    precio_kg:     Decimal
    total_value:   Decimal

    recycler_id:   uuid.UUID | None
    weighing_id:   uuid.UUID | None
    buyer_name:    str | None
    buyer_nit:     str | None
    buyer_email:   str | None

    fecha:      datetime
    created_at: datetime

    material:  MaterialResponse
    warehouse: WarehouseResponse
    recycler:  RecyclerSummary | None


class TransactionListResponse(BaseModel):
    total: int
    items: list[TransactionResponse]


class CreateVentaRequest(BaseModel):
    material_code: str
    warehouse_id:  uuid.UUID
    kg:            Decimal
    precio_kg:     Decimal
    buyer_name:    str | None = None
    buyer_nit:     str | None = None
    buyer_email:   str | None = None

    @field_validator("kg", "precio_kg")
    @classmethod
    def must_be_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("El valor debe ser mayor que cero")
        return v


class UpdateTransactionStatusRequest(BaseModel):
    status: TransactionStatus


class TransactionStatsResponse(BaseModel):
    total_compras_month:  int
    total_ventas_month:   int
    total_kg_compras:     Decimal
    total_kg_ventas:      Decimal
    total_value_compras:  Decimal
    total_value_ventas:   Decimal
    pending_count:        int
