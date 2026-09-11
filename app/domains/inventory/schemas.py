import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


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


class InventoryItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:                  uuid.UUID
    material_code:       str
    warehouse_id:        uuid.UUID
    stock_kg:            Decimal
    stock_min_kg:        Decimal
    precio_kg:           Decimal
    fecha_actualizacion: datetime
    estado:              str
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
    precio_kg:    Decimal | None = None
