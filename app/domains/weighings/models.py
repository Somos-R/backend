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
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.domains.inventory.models import Material, Warehouse
    from app.domains.users.models import User


class WeighingStatus(str, enum.Enum):
    pending_validation = "pending_validation"
    validated  = "validated"
    paid    = "paid"
    rejected = "rejected"


class AffiliationStatus(str, enum.Enum):
    """How the person who delivered the material relates to the ECA that received it.

    An ECA receives material whoever brings it (non-discrimination); what changes is where the weighing
    goes afterwards. Only `linked` weighings reach an association.
    """

    linked = "linked"  # a verified recycler of an association actively linked to the ECA
    unlinked_association = "unlinked_association"  # a recycler whose association is not linked to the ECA
    independent = "independent"  # a recycler with no association, or a person who is not in Somos R


class Weighing(Base):
    __tablename__ = "weighings"
    __table_args__ = (
        CheckConstraint("kg > 0", name="ck_weighings_kg_positive"),
        # Someone delivered it: a registered recycler, or a person identified by name and document.
        CheckConstraint(
            "recycler_id IS NOT NULL OR (seller_name IS NOT NULL AND seller_id_type IS NOT NULL "
            "AND seller_id_number IS NOT NULL)", name="ck_weighings_has_seller"),
        CheckConstraint("price_per_kg > 0", name="ck_weighings_price_per_kg_positive"),
        # A recycler's history filtered by state, newest first.
        Index("ix_weighings_recycler_status_occurred", "recycler_id", "status", "occurred_at"),
    )

    id:               Mapped[uuid.UUID]       = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recycler_id:      Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    # A person who sells material and is not registered (a recycler outside Somos R, a private person):
    # the minimum to identify them.
    seller_name:      Mapped[str | None]      = mapped_column(String(255), nullable=True)
    seller_id_type:   Mapped[str | None]      = mapped_column(String(10), ForeignKey("document_types.code"), nullable=True)
    seller_id_number: Mapped[str | None]      = mapped_column(String(20), nullable=True)
    # Fixed when the weighing is registered: a later change of the link does not rewrite history.
    affiliation_status: Mapped[AffiliationStatus] = mapped_column(
        Enum(AffiliationStatus, name="affiliation_status"), nullable=False)
    material_code:    Mapped[str]             = mapped_column(String(30), ForeignKey("materials.code"), nullable=False)
    warehouse_id:     Mapped[uuid.UUID]       = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False)
    kg:               Mapped[Decimal]         = mapped_column(Numeric(10, 2), nullable=False)
    price_per_kg:        Mapped[Decimal]         = mapped_column(Numeric(10, 2), nullable=False)
    status:           Mapped[WeighingStatus]  = mapped_column(Enum(WeighingStatus), nullable=False, default=WeighingStatus.pending_validation)
    rejection_reason: Mapped[str | None]      = mapped_column(Text, nullable=True)
    validated_by:     Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    validated_at:     Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    occurred_at:            Mapped[datetime]        = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at:       Mapped[datetime]        = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at:       Mapped[datetime]        = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    recycler:  Mapped["User | None"] = relationship("User", foreign_keys=[recycler_id])
    validator: Mapped["User | None"] = relationship("User", foreign_keys=[validated_by])
    material:  Mapped["Material"] = relationship("Material")
    warehouse: Mapped["Warehouse"] = relationship("Warehouse")

    @property
    def total_value(self) -> Decimal:
        return self.kg * self.price_per_kg
