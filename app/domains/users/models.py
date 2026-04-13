import uuid
from datetime import datetime

from geoalchemy2 import Geometry
from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.domains.users.enums import DocumentType, UserType, VerificationStatus


class User(Base):
    __tablename__ = "users"

    # --- Common fields ---
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    id_type: Mapped[DocumentType] = mapped_column(Enum(DocumentType), nullable=False)
    id_number: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    user_type: Mapped[UserType] = mapped_column(Enum(UserType), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # --- citizen / building_admin ---
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)

    # --- building_admin ---
    building_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    num_units: Mapped[int | None] = mapped_column(nullable=True)
    representation_document: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- recycler ---
    profile_picture: Mapped[str | None] = mapped_column(Text, nullable=True)
    id_picture: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_status: Mapped[VerificationStatus | None] = mapped_column(
        Enum(VerificationStatus), nullable=True
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    verifier: Mapped["User | None"] = relationship("User", remote_side="User.id", foreign_keys=[verified_by])

    # --- eca_operator ---
    employee_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    permissions: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # --- asobeum_admin ---
    association_nit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    legal_representative: Mapped[str | None] = mapped_column(String(255), nullable=True)
    coverage_area: Mapped[object | None] = mapped_column(
        Geometry(geometry_type="POLYGON", srid=4326, spatial_index=False), nullable=True
    )

    # --- b2b_client ---
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tax_id: Mapped[str | None] = mapped_column(String(50), unique=True, nullable=True)
    commercial_contact: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rep_goals: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # --- recycler / eca_operator / asobeum_admin ---
    association_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
