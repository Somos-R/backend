import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.domains.inventory.models import Material, Warehouse
    from app.domains.users.models import User


class TransactionType(str, enum.Enum):
    purchase = "purchase"
    sale  = "sale"


class TransactionStatus(str, enum.Enum):
    pending  = "pending"
    paid     = "paid"
    cancelled  = "cancelled"
    delivered  = "delivered"


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        # A weighing produces at most one purchase (NULLs, i.e. sales, are not constrained).
        UniqueConstraint("weighing_id", name="uq_transactions_weighing_id"),
        CheckConstraint("kg > 0", name="ck_transactions_kg_positive"),
        CheckConstraint("price_per_kg > 0", name="ck_transactions_price_per_kg_positive"),
        Index("ix_transactions_type_status_occurred", "type", "status", "occurred_at"),
    )

    id:            Mapped[uuid.UUID]         = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    type:          Mapped[TransactionType]   = mapped_column(Enum(TransactionType), nullable=False)
    status:        Mapped[TransactionStatus] = mapped_column(Enum(TransactionStatus), nullable=False, default=TransactionStatus.pending)
    material_code: Mapped[str]               = mapped_column(String(30), ForeignKey("materials.code"), nullable=False)
    warehouse_id:  Mapped[uuid.UUID]         = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False)
    kg:            Mapped[Decimal]           = mapped_column(Numeric(10, 2), nullable=False)
    price_per_kg:     Mapped[Decimal]           = mapped_column(Numeric(10, 2), nullable=False)

    # purchase: reciclador que entregó el material
    recycler_id:  Mapped[uuid.UUID | None]  = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    weighing_id:  Mapped[uuid.UUID | None]  = mapped_column(UUID(as_uuid=True), ForeignKey("weighings.id"), nullable=True)

    # sale: datos del comprador
    buyer_name:  Mapped[str | None]  = mapped_column(String(255), nullable=True)
    buyer_nit:   Mapped[str | None]  = mapped_column(String(20), nullable=True)
    buyer_email: Mapped[str | None]  = mapped_column(String(255), nullable=True)

    occurred_at:      Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    material:  Mapped["Material"]      = relationship("Material")       
    warehouse: Mapped["Warehouse"]     = relationship("Warehouse")      
    recycler:  Mapped["User | None"]   = relationship("User", foreign_keys=[recycler_id]) 
    creator:   Mapped["User"]          = relationship("User", foreign_keys=[created_by])  

    @property
    def total_value(self) -> Decimal:
        return self.kg * self.price_per_kg
