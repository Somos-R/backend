import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_validator

from app.domains.inventory.schemas import MaterialResponse, WarehouseResponse
from app.domains.transactions.models import TransactionStatus, TransactionType


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
    price_per_kg:     Decimal
    total_value:   Decimal

    recycler_id:   uuid.UUID | None
    weighing_id:   uuid.UUID | None
    buyer_name:    str | None
    buyer_nit:     str | None
    buyer_email:   str | None

    occurred_at:      datetime
    created_at: datetime

    material:  MaterialResponse
    warehouse: WarehouseResponse
    recycler:  RecyclerSummary | None


class TransactionListResponse(BaseModel):
    total: int
    items: list[TransactionResponse]


class CreateSaleRequest(BaseModel):
    material_code: str
    warehouse_id:  uuid.UUID
    kg:            Decimal
    price_per_kg:     Decimal
    buyer_name:    str | None = None
    buyer_nit:     str | None = None
    buyer_email:   str | None = None

    @field_validator("kg", "price_per_kg")
    @classmethod
    def must_be_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("El valor debe ser mayor que cero")
        return v


class UpdateTransactionStatusRequest(BaseModel):
    status: TransactionStatus


class TransactionStatsResponse(BaseModel):
    total_purchases_month:  int
    total_sales_month:   int
    total_kg_purchases:     Decimal
    total_kg_sales:      Decimal
    total_value_purchases:  Decimal
    total_value_sales:   Decimal
    pending_count:        int
