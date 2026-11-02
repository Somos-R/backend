"""inventory_movements.reason: why a movement happened

A nullable column and nothing else, so no existing row changes. A loss (shrinkage) always carries its reason; the
other movement types may leave it empty. The ledger stays append-only: the trigger that rejects UPDATE and DELETE
is untouched.

Revision ID: 0029
Revises: 0028
Create Date: 2026-11-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: Union[str, Sequence[str], None] = "0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("inventory_movements", sa.Column("reason", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("inventory_movements", "reason")
