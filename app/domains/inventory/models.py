import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
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
    # The ECA that owns the warehouse, and with it the inventory, weighings and transactions that happen
    # in it. A warehouse with no owner is invisible to every customer.
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), index=True, nullable=True)
    created_at: Mapped[datetime]  = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    items: Mapped[list["InventoryItem"]] = relationship("InventoryItem", back_populates="warehouse")


class InventoryItem(Base):
    __tablename__ = "inventory_items"
    __table_args__ = (
        UniqueConstraint("material_code", "warehouse_id", name="uq_inventory_material_warehouse"),
        CheckConstraint("stock_kg >= 0", name="ck_inventory_stock_non_negative"),
        CheckConstraint("stock_min_kg >= 0", name="ck_inventory_stock_min_non_negative"),
        CheckConstraint("price_per_kg >= 0", name="ck_inventory_price_per_kg_non_negative"),
    )

    id:                  Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    material_code:       Mapped[str]       = mapped_column(String(30), ForeignKey("materials.code"), nullable=False)
    warehouse_id:        Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False)
    stock_kg:            Mapped[Decimal]   = mapped_column(Numeric(12, 2), nullable=False, default=0)
    stock_min_kg:        Mapped[Decimal]   = mapped_column(Numeric(12, 2), nullable=False, default=50)
    price_per_kg:           Mapped[Decimal]   = mapped_column(Numeric(10, 2), nullable=False, default=0)
    updated_at: Mapped[datetime]  = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    material:  Mapped["Material"]  = relationship("Material")
    warehouse: Mapped["Warehouse"] = relationship("Warehouse", back_populates="items")

    @hybrid_property
    def status(self) -> str:
        if self.stock_kg == 0:
            return "out_of_stock"
        if self.stock_kg < self.stock_min_kg:
            return "low_stock"
        return "available"

    @status.inplace.expression
    @classmethod
    def _status_expression(cls):
        # Same rule as above, evaluated by the database (filters and counts).
        return case(
            (cls.stock_kg == 0, "out_of_stock"),
            (cls.stock_kg < cls.stock_min_kg, "low_stock"),
            else_="available",
        )

    @property
    def total_value(self) -> Decimal:
        return self.stock_kg * self.price_per_kg


class InventoryMovement(Base):
    """One entry or exit of material in a warehouse, with the balance right after it.

    The ledger of the stock: append-only (a database trigger rejects UPDATE and DELETE), so a mistake is
    corrected with a new movement. `seq` gives the ledger a total order, because several movements of one
    transaction share their timestamp.
    """

    __tablename__ = "inventory_movements"
    __table_args__ = (
        CheckConstraint(
            "movement_type IN ('opening', 'purchase', 'sale', 'sale_cancellation', 'adjustment', 'loss')",
            name="ck_inventory_movements_type"),
        CheckConstraint("kg_delta <> 0", name="ck_inventory_movements_delta_not_zero"),
        CheckConstraint("balance_after_kg >= 0", name="ck_inventory_movements_balance_non_negative"),
        Index("ix_inventory_movements_item", "warehouse_id", "material_code", "seq"),
        Index("ix_inventory_movements_source", "source_type", "source_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), unique=True, nullable=False)
    material_code: Mapped[str] = mapped_column(String(30), ForeignKey("materials.code"), nullable=False)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False)
    movement_type: Mapped[str] = mapped_column(String(20), nullable=False)
    kg_delta: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    balance_after_kg: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    price_per_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    source_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    material: Mapped["Material"] = relationship("Material")
    warehouse: Mapped["Warehouse"] = relationship("Warehouse")
