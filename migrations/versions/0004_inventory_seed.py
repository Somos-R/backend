"""inventory: materials, warehouses, inventory_items

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-21

"""
from typing import Sequence, Union
import uuid

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- materials ---
    materials = op.create_table(
        "materials",
        sa.Column("code",      sa.String(30),  primary_key=True),
        sa.Column("label",     sa.String(100), nullable=False),
        sa.Column("unit",      sa.String(10),  nullable=False, server_default="kg"),
        sa.Column("is_active", sa.Boolean(),   nullable=False, server_default="true"),
    )
    op.bulk_insert(materials, [
        {"code": "papel",       "label": "Papel y Cartón",       "unit": "kg", "is_active": True},
        {"code": "plastico",    "label": "Plástico",             "unit": "kg", "is_active": True},
        {"code": "vidrio",      "label": "Vidrio",               "unit": "kg", "is_active": True},
        {"code": "metal",       "label": "Metal",                "unit": "kg", "is_active": True},
        {"code": "carton",      "label": "Cartón",               "unit": "kg", "is_active": True},
        {"code": "electronico", "label": "Residuo Electrónico",  "unit": "kg", "is_active": True},
        {"code": "organico",    "label": "Orgánico",             "unit": "kg", "is_active": True},
    ])

    # --- warehouses ---
    warehouses = op.create_table(
        "warehouses",
        sa.Column("id",        sa.UUID(),      primary_key=True),
        sa.Column("name",      sa.String(100), nullable=False),
        sa.Column("address",   sa.Text(),      nullable=True),
        sa.Column("is_active", sa.Boolean(),   nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    warehouse_ids = [str(uuid.uuid4()) for _ in range(3)]
    op.bulk_insert(warehouses, [
        {"id": warehouse_ids[0], "name": "Bodega Norte",   "address": "Calle 100 #15-20, Bogotá",        "is_active": True},
        {"id": warehouse_ids[1], "name": "Bodega Sur",     "address": "Carrera 50 #30-10, Bogotá",       "is_active": True},
        {"id": warehouse_ids[2], "name": "Punto Central",  "address": "Avenida El Dorado #68-11, Bogotá", "is_active": True},
    ])

    # --- inventory_items ---
    op.create_table(
        "inventory_items",
        sa.Column("id",                   sa.UUID(),       primary_key=True),
        sa.Column("material_code",        sa.String(30),   sa.ForeignKey("materials.code"),   nullable=False),
        sa.Column("warehouse_id",         sa.UUID(),       sa.ForeignKey("warehouses.id"),    nullable=False),
        sa.Column("stock_kg",             sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("stock_min_kg",         sa.Numeric(12, 2), nullable=False, server_default="50"),
        sa.Column("precio_kg",            sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("fecha_actualizacion",  sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("material_code", "warehouse_id", name="uq_inventory_material_warehouse"),
    )
    op.create_index("idx_inventory_material", "inventory_items", ["material_code"])
    op.create_index("idx_inventory_warehouse", "inventory_items", ["warehouse_id"])


def downgrade() -> None:
    op.drop_table("inventory_items")
    op.drop_table("warehouses")
    op.drop_table("materials")
