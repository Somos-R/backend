"""transactions table

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-21

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "transactions",
        sa.Column("id",            postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("type",          sa.Enum("compra", "venta",          name="transactiontype"),   nullable=False),
        sa.Column("status",        sa.Enum("pendiente", "pagado", "cancelado", "entregado", name="transactionstatus"), nullable=False, server_default="pendiente"),
        sa.Column("material_code", sa.String(30),  sa.ForeignKey("materials.code"),   nullable=False),
        sa.Column("warehouse_id",  postgresql.UUID(as_uuid=True), sa.ForeignKey("warehouses.id"), nullable=False),
        sa.Column("kg",            sa.Numeric(10, 2), nullable=False),
        sa.Column("precio_kg",     sa.Numeric(10, 2), nullable=False),

        sa.Column("recycler_id",   postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("weighing_id",   postgresql.UUID(as_uuid=True), sa.ForeignKey("weighings.id"), nullable=True),

        sa.Column("buyer_name",    sa.String(255), nullable=True),
        sa.Column("buyer_nit",     sa.String(20),  nullable=True),
        sa.Column("buyer_email",   sa.String(255), nullable=True),

        sa.Column("fecha",      sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )

    op.create_index("ix_transactions_type",   "transactions", ["type"])
    op.create_index("ix_transactions_status", "transactions", ["status"])
    op.create_index("ix_transactions_fecha",  "transactions", ["fecha"])


def downgrade() -> None:
    op.drop_table("transactions")
    sa.Enum(name="transactionstatus").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="transactiontype").drop(op.get_bind(), checkfirst=True)
