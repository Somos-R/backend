import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class WeighingStatus(str, enum.Enum):
    pendiente = "pendiente"
    validado  = "validado"
    pagado    = "pagado"
    rechazado = "rechazado"


class Weighing(Base):
    __tablename__ = "weighings"

    id:               Mapped[uuid.UUID]       = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recycler_id:      Mapped[uuid.UUID]       = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    material_code:    Mapped[str]             = mapped_column(String(30), ForeignKey("materials.code"), nullable=False)
    warehouse_id:     Mapped[uuid.UUID]       = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False)
    kg:               Mapped[Decimal]         = mapped_column(Numeric(10, 2), nullable=False)
    precio_kg:        Mapped[Decimal]         = mapped_column(Numeric(10, 2), nullable=False)
    estado:           Mapped[WeighingStatus]  = mapped_column(Enum(WeighingStatus), nullable=False, default=WeighingStatus.pendiente)
    rejection_reason: Mapped[str | None]      = mapped_column(Text, nullable=True)
    validated_by:     Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    validated_at:     Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fecha:            Mapped[datetime]        = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at:       Mapped[datetime]        = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at:       Mapped[datetime]        = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    recycler:  Mapped["User"] = relationship("User", foreign_keys=[recycler_id])  # type: ignore[name-defined]
    validator: Mapped["User | None"] = relationship("User", foreign_keys=[validated_by])  # type: ignore[name-defined]
    material:  Mapped["Material"] = relationship("Material")  # type: ignore[name-defined]
    warehouse: Mapped["Warehouse"] = relationship("Warehouse")  # type: ignore[name-defined]

    @property
    def total_value(self) -> Decimal:
        return self.kg * self.precio_kg
