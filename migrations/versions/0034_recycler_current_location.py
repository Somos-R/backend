"""recycler current location: where the recycler is now, and when the app said so

`users.current_location` is the last position the recycler's app reported (a geography point, GiST-indexed like the
other points) and `users.location_updated_at` is the device time of that report: an offline app that replays older
positions must not overwrite a newer one. Both are nullable and nothing existing changes. `users.start_location`
(where the day starts, migration 0032) is unchanged.

Downgrade drops both columns and whatever was stored in them.

Revision ID: 0034
Revises: 0033
Create Date: 2026-11-04

"""
from typing import Sequence, Union

import geoalchemy2
import sqlalchemy as sa
from alembic import op

revision: str = "0034"
down_revision: Union[str, Sequence[str], None] = "0033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column(
        "current_location", geoalchemy2.types.Geography(geometry_type="POINT", srid=4326, spatial_index=False),
        nullable=True))
    op.add_column("users", sa.Column("location_updated_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("CREATE INDEX ix_users_current_location ON users USING GIST (current_location)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_users_current_location")
    op.drop_column("users", "location_updated_at")
    op.drop_column("users", "current_location")
