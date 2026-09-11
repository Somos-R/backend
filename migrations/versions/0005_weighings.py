"""weighings table

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-21

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "weighings",
        sa.Column("id",               sa.UUID(),        primary_key=True),
        sa.Column("recycler_id",      sa.UUID(),        sa.ForeignKey("users.id"),           nullable=False),
        sa.Column("material_code",    sa.String(30),    sa.ForeignKey("materials.code"),     nullable=False),
        sa.Column("warehouse_id",     sa.UUID(),        sa.ForeignKey("warehouses.id"),      nullable=False),
        sa.Column("kg",               sa.Numeric(10, 2), nullable=False),
        sa.Column("precio_kg",        sa.Numeric(10, 2), nullable=False),
        sa.Column(
            "estado",
            sa.Enum("pendiente", "validado", "pagado", "rechazado", name="weighingstatus"),
            nullable=False,
            server_default="pendiente",
        ),
        sa.Column("rejection_reason", sa.Text(),        nullable=True),
        sa.Column("validated_by",     sa.UUID(),        sa.ForeignKey("users.id"),           nullable=True),
        sa.Column("validated_at",     sa.DateTime(timezone=True), nullable=True),
        sa.Column("fecha",            sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at",       sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at",       sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_weighings_recycler",  "weighings", ["recycler_id"])
    op.create_index("idx_weighings_material",  "weighings", ["material_code"])
    op.create_index("idx_weighings_estado",    "weighings", ["estado"])
    op.create_index("idx_weighings_fecha",     "weighings", ["fecha"])


def downgrade() -> None:
    op.drop_table("weighings")
    op.execute("DROP TYPE IF EXISTS weighingstatus")
