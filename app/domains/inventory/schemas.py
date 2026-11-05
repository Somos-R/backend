import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


def unique_material_codes(codes: list[str]) -> list[str]:
    """Each material once, in the order given; a code never longer than the catalog allows (`materials.code`).

    Shape only: whether each code exists and is active is checked against the catalog by `ensure_material_is_active`.
    """
    if any(not code or len(code) > 30 for code in codes):
        raise ValueError("Cada material debe ser un código de 1 a 30 caracteres")
    return list(dict.fromkeys(codes))


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
    # Same width and decimals as the columns, and never negative (the table refuses it anyway).
    stock_min_kg: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    price_per_kg:    Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=2)


class RecordLossRequest(BaseModel):
    kg: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    reason: str = Field(min_length=3, max_length=200)


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
    reason: str | None  # why, when the movement asks for it (a loss always has one)


class MovementListResponse(BaseModel):
    total: int
    items: list[MovementResponse]
