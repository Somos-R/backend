import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class TransactionType(str, enum.Enum):
    compra = "compra"
    venta  = "venta"


class TransactionStatus(str, enum.Enum):
    pendiente  = "pendiente"
    pagado     = "pagado"
    cancelado  = "cancelado"
    entregado  = "entregado"


class Transaction(Base):
    __tablename__ = "transactions"

    id:            Mapped[uuid.UUID]         = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    type:          Mapped[TransactionType]   = mapped_column(Enum(TransactionType), nullable=False)
    status:        Mapped[TransactionStatus] = mapped_column(Enum(TransactionStatus), nullable=False, default=TransactionStatus.pendiente)
    material_code: Mapped[str]               = mapped_column(String(30), ForeignKey("materials.code"), nullable=False)
    warehouse_id:  Mapped[uuid.UUID]         = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False)
    kg:            Mapped[Decimal]           = mapped_column(Numeric(10, 2), nullable=False)
    precio_kg:     Mapped[Decimal]           = mapped_column(Numeric(10, 2), nullable=False)

    # compra: reciclador que entregó el material
    recycler_id:  Mapped[uuid.UUID | None]  = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    weighing_id:  Mapped[uuid.UUID | None]  = mapped_column(UUID(as_uuid=True), ForeignKey("weighings.id"), nullable=True)

    # venta: datos del comprador
    buyer_name:  Mapped[str | None]  = mapped_column(String(255), nullable=True)
    buyer_nit:   Mapped[str | None]  = mapped_column(String(20), nullable=True)
    buyer_email: Mapped[str | None]  = mapped_column(String(255), nullable=True)

    fecha:      Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    material:  Mapped["Material"]      = relationship("Material")         # type: ignore[name-defined]
    warehouse: Mapped["Warehouse"]     = relationship("Warehouse")        # type: ignore[name-defined]
    recycler:  Mapped["User | None"]   = relationship("User", foreign_keys=[recycler_id])   # type: ignore[name-defined]
    creator:   Mapped["User"]          = relationship("User", foreign_keys=[created_by])    # type: ignore[name-defined]

    @property
    def total_value(self) -> Decimal:
        return self.kg * self.precio_kg
