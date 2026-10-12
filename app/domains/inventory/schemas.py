import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class MaterialResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    code:  str
    label: str
    unit:  str


class WarehouseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id:      uuid.UUID
    name:    str
    address: str | None


class CreateWarehouseRequest(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    address: str | None = Field(default=None, max_length=500)


class InventoryItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:                  uuid.UUID
    material_code:       str
    warehouse_id:        uuid.UUID
    stock_kg:            Decimal
    stock_min_kg:        Decimal
    price_per_kg:           Decimal
    updated_at: datetime
    status:              str
    total_value:         Decimal
    material:            MaterialResponse
    warehouse:           WarehouseResponse


class InventoryListResponse(BaseModel):
    total: int
    items: list[InventoryItemResponse]


class InventoryStatsResponse(BaseModel):
    total_stock_kg:    Decimal
    total_value:       Decimal
    available_count:   int
    low_stock_count:   int
    out_of_stock_count: int


class UpdateInventoryItemRequest(BaseModel):
    stock_min_kg: Decimal | None = None
    price_per_kg:    Decimal | None = None


class MovementResponse(BaseModel):
    """One entry or exit of material. `kg_delta` is positive when material comes in and negative when it goes out."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    occurred_at: datetime
    movement_type: str  # opening, purchase, sale, sale_cancellation, adjustment, loss
    material: MaterialResponse
    warehouse: WarehouseResponse
    kg_delta: Decimal
    balance_after_kg: Decimal
    price_per_kg: Decimal | None
    source_type: str | None  # weighing or transaction: what caused it
    source_id: uuid.UUID | None
    actor_id: uuid.UUID | None  # who did it (an id only: names of another organization's staff are not shown)


class MovementListResponse(BaseModel):
    total: int
    items: list[MovementResponse]
