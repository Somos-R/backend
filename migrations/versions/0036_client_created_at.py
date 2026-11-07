"""client_created_at: when the device saved a weighing or a pickup request that arrived through POST /sync/batch

Nullable timestamptz on `weighings` and `collections`. NULL means the record was created online, or before this
column existed. It is the device's own clock, so it is kept as a fact about the record and never used as the
record's date (`occurred_at` and `created_at` stay the server's). It is not part of the weighing integrity hash.

Additive only. Downgrade drops both columns.

Revision ID: 0036
Revises: 0035
Create Date: 2026-11-08

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0036"
down_revision: Union[str, Sequence[str], None] = "0035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ("weighings", "collections")


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column("client_created_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for table in TABLES:
        op.drop_column(table, "client_created_at")
