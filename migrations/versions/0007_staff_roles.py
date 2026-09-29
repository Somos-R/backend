"""roles: association operator, route manager, ECA warehouse manager

Completes the staff roles described in the platform technical document.
Existing roles (eca_admin, eca_operator, association_admin) are unchanged.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-28

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_ROLES = [
    {"code": "association_operator", "label": "Operativo Asociación",   "is_active": True},
    {"code": "route_manager",        "label": "Encargado de rutas",     "is_active": True},
    {"code": "eca_warehouse",        "label": "Encargado de bodega ECA", "is_active": True},
]


def upgrade() -> None:
    roles = sa.table(
        "roles",
        sa.column("code", sa.String),
        sa.column("label", sa.String),
        sa.column("is_active", sa.Boolean),
    )
    op.bulk_insert(roles, NEW_ROLES)


def downgrade() -> None:
    codes = ", ".join(f"'{r['code']}'" for r in NEW_ROLES)
    op.execute(f"UPDATE users SET role_code = NULL WHERE role_code IN ({codes})")
    op.execute(f"DELETE FROM roles WHERE code IN ({codes})")
