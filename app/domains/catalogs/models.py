from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DocumentType(Base):
    __tablename__ = "document_types"

    code: Mapped[str] = mapped_column(String(10), primary_key=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class UserType(Base):
    __tablename__ = "user_types"

    code: Mapped[str] = mapped_column(String(20), primary_key=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Role(Base):
    __tablename__ = "roles"

    code: Mapped[str] = mapped_column(String(20), primary_key=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
