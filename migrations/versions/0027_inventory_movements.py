"""inventory_movements: the append-only ledger of every change to the stock

Until now the stock of a material in a warehouse was one number that changed in place, so nothing told how it got
there. Every entry or exit is now a row: how many kilos, why (a purchase from a validated weighing, a sale, a
cancelled sale, an adjustment, a loss), what caused it, who did it and the balance right after. Like the audit
log, a trigger rejects UPDATE and DELETE: a mistake is corrected by a new movement, never by editing history.

Stock that already exists gets an `opening` movement, so the ledger of every item adds up to its stock.

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-09

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0027"
down_revision: Union[str, Sequence[str], None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TYPES = ("opening", "purchase", "sale", "sale_cancellation", "adjustment", "loss")


def upgrade() -> None:
    op.create_table(
        "inventory_movements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # A total order for the ledger: two movements of one transaction share their timestamp.
        sa.Column("seq", sa.BigInteger, sa.Identity(always=True), nullable=False, unique=True),
        sa.Column("material_code", sa.String(30), sa.ForeignKey("materials.code"), nullable=False),
        sa.Column("warehouse_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("warehouses.id"), nullable=False),
        sa.Column("movement_type", sa.String(20), nullable=False),
        sa.Column("kg_delta", sa.Numeric(12, 2), nullable=False),
        sa.Column("balance_after_kg", sa.Numeric(12, 2), nullable=False),
        sa.Column("price_per_kg", sa.Numeric(10, 2), nullable=True),
        sa.Column("source_type", sa.String(20), nullable=True),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("movement_type IN ('" + "', '".join(TYPES) + "')", name="ck_inventory_movements_type"),
        sa.CheckConstraint("kg_delta <> 0", name="ck_inventory_movements_delta_not_zero"),
        sa.CheckConstraint("balance_after_kg >= 0", name="ck_inventory_movements_balance_non_negative"),
    )
    op.create_index("ix_inventory_movements_item", "inventory_movements",
                    ["warehouse_id", "material_code", "seq"])
    op.create_index("ix_inventory_movements_source", "inventory_movements", ["source_type", "source_id"])

    op.execute("""
        CREATE FUNCTION inventory_movements_reject_changes() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'inventory_movements is append-only';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER inventory_movements_append_only
        BEFORE UPDATE OR DELETE ON inventory_movements
        FOR EACH ROW EXECUTE FUNCTION inventory_movements_reject_changes()
    """)

    # What is already in stock has no history: it enters the ledger as an opening balance.
    op.execute("""
        INSERT INTO inventory_movements
            (id, material_code, warehouse_id, movement_type, kg_delta, balance_after_kg, price_per_kg)
        SELECT gen_random_uuid(), material_code, warehouse_id, 'opening', stock_kg, stock_kg, price_per_kg
        FROM inventory_items
        WHERE stock_kg > 0
        ORDER BY id
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS inventory_movements_append_only ON inventory_movements")
    op.execute("DROP FUNCTION IF EXISTS inventory_movements_reject_changes()")
    op.drop_index("ix_inventory_movements_source", table_name="inventory_movements")
    op.drop_index("ix_inventory_movements_item", table_name="inventory_movements")
    op.drop_table("inventory_movements")
