"""platform actor: user type `platform` and role `platform_admin`

Somos R itself becomes an actor of the system (the two people who review organizations and manage
users). There is no self-registration for it: accounts are created by an operator script
(scripts/create_platform_admin.py) or by another platform admin.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-29

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: Union[str, Sequence[str], None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    user_types = sa.table(
        "user_types",
        sa.column("code", sa.String), sa.column("label", sa.String), sa.column("is_active", sa.Boolean),
    )
    roles = sa.table(
        "roles",
        sa.column("code", sa.String), sa.column("label", sa.String), sa.column("is_active", sa.Boolean),
    )
    op.bulk_insert(user_types, [{"code": "platform", "label": "Somos R", "is_active": True}])
    op.bulk_insert(roles, [{"code": "platform_admin", "label": "Administrador de Somos R", "is_active": True}])


def downgrade() -> None:
    op.execute("DELETE FROM users WHERE user_type_code = 'platform'")
    op.execute("DELETE FROM roles WHERE code = 'platform_admin'")
    op.execute("DELETE FROM user_types WHERE code = 'platform'")
