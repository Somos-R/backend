"""bag_count: how many bags a pickup request is for

Nullable smallint on `collections`, with a check that it is positive. NULL means the requester did not say, which
is what every request created before this column holds. It lets a building or a JAC size its request.

Additive only. Downgrade drops the column and its check.

Revision ID: 0037
Revises: 0036
Create Date: 2026-11-18

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0037"
down_revision: Union[str, Sequence[str], None] = "0036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("collections", sa.Column("bag_count", sa.SmallInteger(), nullable=True))
    op.create_check_constraint("ck_collections_bag_count_positive", "collections", "bag_count > 0")


def downgrade() -> None:
    op.drop_constraint("ck_collections_bag_count_positive", "collections", type_="check")
    op.drop_column("collections", "bag_count")
