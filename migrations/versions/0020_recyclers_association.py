"""recyclers belong to an association

A recycler now has an association (`users.organization_id`, an organization of type `association`): it is
who verifies them and the only association staff who see them. This gives one to the recyclers that
already exist.

Data migration, deterministic: when there is exactly ONE approved association, the recyclers that have
none join it (unambiguous). Otherwise they are left without one, and a warning says how many, to be
assigned from the backoffice (`PUT /admin/users/{id}/organization`). Nothing is guessed.

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-30

"""
import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: Union[str, Sequence[str], None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    bind = op.get_bind()
    associations = bind.execute(sa.text(
        "SELECT id FROM organizations WHERE type::text = 'association' AND status::text = 'approved'")).all()
    if len(associations) == 1:
        bind.execute(sa.text(
            "UPDATE users SET organization_id = :org WHERE user_type_code = 'recycler' AND organization_id IS NULL"),
            {"org": associations[0][0]})
    orphans = bind.execute(sa.text(
        "SELECT count(*) FROM users WHERE user_type_code = 'recycler' AND organization_id IS NULL")).scalar()
    if orphans:
        log.warning("0020: %s recycler(s) left without an association (%s approved associations exist); "
                    "assign them from the backoffice", orphans, len(associations))


def downgrade() -> None:
    # The column stays (it belongs to 0018); only what this migration filled in is cleared.
    op.execute("UPDATE users SET organization_id = NULL WHERE user_type_code = 'recycler'")
