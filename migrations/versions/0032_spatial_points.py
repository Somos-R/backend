"""spatial points: geography for pickup requests, recycler start points and ECA locations

- `collections.location` changes from `geometry(Point, 4326)` to `geography(Point, 4326)`, so radii and
  distances are meters. Lossless both ways: the same SRID 4326 coordinates are kept, only the type that
  interprets them changes. Its GiST index is rebuilt for the new type.
- `users.start_location` (where a recycler's day starts) and `organizations.location` (an ECA's drop-off
  point) are new, nullable, GiST-indexed columns. Nothing fills them yet; existing rows stay as they are.

Downgrade converts the collections column back to geometry and drops the two new columns (and whatever
was stored in them after the upgrade).

Revision ID: 0032
Revises: 0031
Create Date: 2026-11-02

"""
from typing import Sequence, Union

import geoalchemy2
import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: Union[str, Sequence[str], None] = "0031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_POINT_COLUMNS = (
    ("users", "start_location", "ix_users_start_location"),
    ("organizations", "location", "ix_organizations_location"),
)


def _point() -> geoalchemy2.types.Geography:
    return geoalchemy2.types.Geography(geometry_type="POINT", srid=4326, spatial_index=False)


def upgrade() -> None:
    # The index's operator class belongs to geometry: it cannot survive the type change, so it is rebuilt.
    op.execute("DROP INDEX ix_collections_location")
    op.execute("ALTER TABLE collections ALTER COLUMN location TYPE geography(Point, 4326) "
               "USING location::geography")
    op.execute("CREATE INDEX ix_collections_location ON collections USING GIST (location)")

    for table, column, index in NEW_POINT_COLUMNS:
        op.add_column(table, sa.Column(column, _point(), nullable=True))
        op.execute(f"CREATE INDEX {index} ON {table} USING GIST ({column})")


def downgrade() -> None:
    for table, column, index in NEW_POINT_COLUMNS:
        op.execute(f"DROP INDEX IF EXISTS {index}")
        op.drop_column(table, column)

    op.execute("DROP INDEX ix_collections_location")
    op.execute("ALTER TABLE collections ALTER COLUMN location TYPE geometry(Point, 4326) "
               "USING location::geometry")
    op.execute("CREATE INDEX ix_collections_location ON collections USING GIST (location)")
