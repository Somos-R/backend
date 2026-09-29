import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    case,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Material(Base):
    __tablename__ = "materials"

    code:      Mapped[str]  = mapped_column(String(30), primary_key=True)
    label:     Mapped[str]  = mapped_column(String(100), nullable=False)
    unit:      Mapped[str]  = mapped_column(String(10), nullable=False, default="kg")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Warehouse(Base):
    __tablename__ = "warehouses"

    id:         Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name:       Mapped[str]       = mapped_column(String(100), nullable=False)
    address:    Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active:  Mapped[bool]      = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime]  = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    items: Mapped[list["InventoryItem"]] = relationship("InventoryItem", back_populates="warehouse")


class InventoryItem(Base):
    __tablename__ = "inventory_items"
    __table_args__ = (
        UniqueConstraint("material_code", "warehouse_id", name="uq_inventory_material_warehouse"),
        CheckConstraint("stock_kg >= 0", name="ck_inventory_stock_non_negative"),
        CheckConstraint("stock_min_kg >= 0", name="ck_inventory_stock_min_non_negative"),
        CheckConstraint("precio_kg >= 0", name="ck_inventory_price_non_negative"),
    )

    id:                  Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    material_code:       Mapped[str]       = mapped_column(String(30), ForeignKey("materials.code"), nullable=False)
    warehouse_id:        Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False)
    stock_kg:            Mapped[Decimal]   = mapped_column(Numeric(12, 2), nullable=False, default=0)
    stock_min_kg:        Mapped[Decimal]   = mapped_column(Numeric(12, 2), nullable=False, default=50)
    precio_kg:           Mapped[Decimal]   = mapped_column(Numeric(10, 2), nullable=False, default=0)
    fecha_actualizacion: Mapped[datetime]  = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    material:  Mapped["Material"]  = relationship("Material")
    warehouse: Mapped["Warehouse"] = relationship("Warehouse", back_populates="items")

    @hybrid_property
    def estado(self) -> str:
        if self.stock_kg == 0:
            return "agotado"
        if self.stock_kg < self.stock_min_kg:
            return "bajo_stock"
        return "disponible"

    @estado.inplace.expression
    @classmethod
    def _estado_expression(cls):
        # Same rule as above, evaluated by the database (filters and counts).
        return case(
            (cls.stock_kg == 0, "agotado"),
            (cls.stock_kg < cls.stock_min_kg, "bajo_stock"),
            else_="disponible",
        )

    @property
    def total_value(self) -> Decimal:
        return self.stock_kg * self.precio_kg
