"""inventory integrity: non-negative stock, positive quantities, one purchase per weighing

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-29

Fails (and changes nothing) if existing rows already violate a rule; fix the data first.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0011"
down_revision: Union[str, Sequence[str], None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_check_constraint("ck_inventory_stock_non_negative", "inventory_items", "stock_kg >= 0")
    op.create_check_constraint("ck_inventory_stock_min_non_negative", "inventory_items", "stock_min_kg >= 0")
    op.create_check_constraint("ck_inventory_price_non_negative", "inventory_items", "precio_kg >= 0")

    op.create_check_constraint("ck_weighings_kg_positive", "weighings", "kg > 0")
    op.create_check_constraint("ck_weighings_price_positive", "weighings", "precio_kg > 0")

    op.create_check_constraint("ck_transactions_kg_positive", "transactions", "kg > 0")
    op.create_check_constraint("ck_transactions_price_positive", "transactions", "precio_kg > 0")
    op.create_unique_constraint("uq_transactions_weighing_id", "transactions", ["weighing_id"])


def downgrade() -> None:
    op.drop_constraint("uq_transactions_weighing_id", "transactions", type_="unique")
    op.drop_constraint("ck_transactions_price_positive", "transactions", type_="check")
    op.drop_constraint("ck_transactions_kg_positive", "transactions", type_="check")
    op.drop_constraint("ck_weighings_price_positive", "weighings", type_="check")
    op.drop_constraint("ck_weighings_kg_positive", "weighings", type_="check")
    op.drop_constraint("ck_inventory_price_non_negative", "inventory_items", type_="check")
    op.drop_constraint("ck_inventory_stock_min_non_negative", "inventory_items", type_="check")
    op.drop_constraint("ck_inventory_stock_non_negative", "inventory_items", type_="check")
