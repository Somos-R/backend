"""warehouses belong to an ECA

Weighings, inventory and transactions have no owner of their own: they belong to whoever owns the
warehouse they happened in. Giving each warehouse an owner ECA (`warehouses.organization_id`) is what
lets one ECA's operational data be kept apart from another's.

Data migration, deterministic: when there is exactly ONE approved ECA, the existing warehouses become
its own (unambiguous). Otherwise they are left without an owner, and a warning says how many, to be
assigned from the backoffice (`PUT /admin/warehouses/{id}/organization`). A warehouse with no owner is
invisible to every customer until then. Nothing is guessed.

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-30

"""
import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021"
down_revision: Union[str, Sequence[str], None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    op.add_column("warehouses", sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_warehouses_organization", "warehouses", "organizations", ["organization_id"], ["id"],
                          ondelete="RESTRICT")
    op.create_index("ix_warehouses_organization_id", "warehouses", ["organization_id"])

    bind = op.get_bind()
    ecas = bind.execute(sa.text(
        "SELECT id FROM organizations WHERE type::text = 'eca' AND status::text = 'approved'")).all()
    if len(ecas) == 1:
        bind.execute(sa.text("UPDATE warehouses SET organization_id = :org WHERE organization_id IS NULL"),
                     {"org": ecas[0][0]})
    orphans = bind.execute(sa.text("SELECT count(*) FROM warehouses WHERE organization_id IS NULL")).scalar()
    if orphans:
        log.warning("0021: %s warehouse(s) left without an owner (%s approved ECAs exist); assign them from "
                    "the backoffice", orphans, len(ecas))


def downgrade() -> None:
    op.drop_index("ix_warehouses_organization_id", table_name="warehouses")
    op.drop_constraint("fk_warehouses_organization", "warehouses", type_="foreignkey")
    op.drop_column("warehouses", "organization_id")
