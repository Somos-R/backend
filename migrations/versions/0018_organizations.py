"""organizations: the Association / ECA as an entity, and users.organization_id

Until now an organization only existed as the repeated data of its staff (`association_nit`, ...).
This creates the entity and gives every existing staff account an organization to belong to.

Data migration, deterministic and re-runnable in spirit (it only fills what is empty):
- Association staff are grouped by NIT (trimmed): one organization per NIT. Staff with no NIT
  each get their own organization.
- ECA staff have no organization data at all. Every `eca_admin` becomes its own organization; the
  rest of the ECA staff join it only when it is the ONLY ECA organization (unambiguous). Otherwise
  they are left without one, and a warning says how many, to be assigned by hand.
- The organizations that come from existing accounts are `approved` (they already operate) and get a
  placeholder legal name ("Asociación <NIT>", "ECA de <admin>") to be corrected in the backoffice.

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-29

"""
import logging
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018"
down_revision: Union[str, Sequence[str], None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger("alembic.runtime.migration")

TYPES = ("association", "eca")
STATUSES = ("draft", "submitted", "in_review", "changes_requested", "approved", "rejected", "suspended")


def upgrade() -> None:
    type_enum = postgresql.ENUM(*TYPES, name="organization_type", create_type=False)
    status_enum = postgresql.ENUM(*STATUSES, name="organization_status", create_type=False)
    type_enum.create(op.get_bind(), checkfirst=True)
    status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "organizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("type", type_enum, nullable=False),
        sa.Column("status", status_enum, nullable=False, server_default="draft"),
        sa.Column("legal_name", sa.String(255), nullable=False),
        sa.Column("tax_id", sa.String(50), nullable=True),
        sa.Column("legal_representative", sa.String(255), nullable=True),
        sa.Column("contact_email", sa.String(255), nullable=True),
        sa.Column("contact_phone", sa.String(20), nullable=True),
        sa.Column("address", sa.Text, nullable=True),
        sa.Column("city", sa.String(100), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("uq_organizations_type_tax_id", "organizations", ["type", "tax_id"], unique=True,
                    postgresql_where=sa.text("tax_id IS NOT NULL"))
    op.create_index("ix_organizations_status", "organizations", ["status"])

    op.add_column("users", sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_users_organization", "users", "organizations", ["organization_id"], ["id"],
                          ondelete="RESTRICT")
    op.create_index("ix_users_organization_id", "users", ["organization_id"])

    _assign_existing_staff(op.get_bind())


def _new_organization(bind, org_type: str, legal_name: str, tax_id: str | None, representative: str | None) -> uuid.UUID:
    org_id = uuid.uuid4()
    bind.execute(sa.text(
        "INSERT INTO organizations (id, type, status, legal_name, tax_id, legal_representative, approved_at) "
        "VALUES (:id, :type, 'approved', :name, :tax_id, :rep, now())"
    ), {"id": org_id, "type": org_type, "name": legal_name, "tax_id": tax_id, "rep": representative})
    return org_id


def _assign_existing_staff(bind) -> None:
    # --- Associations: one organization per NIT ---
    nits = bind.execute(sa.text(
        "SELECT btrim(association_nit) AS nit, min(legal_representative) AS rep FROM users "
        "WHERE user_type_code = 'association' AND btrim(coalesce(association_nit, '')) <> '' "
        "GROUP BY btrim(association_nit)"
    )).all()
    for nit, rep in nits:
        org_id = _new_organization(bind, "association", f"Asociación {nit}", nit, rep)
        bind.execute(sa.text(
            "UPDATE users SET organization_id = :org WHERE user_type_code = 'association' "
            "AND btrim(association_nit) = :nit"), {"org": org_id, "nit": nit})
    # Staff without a NIT: each is its own organization.
    for user_id, full_name, rep in bind.execute(sa.text(
        "SELECT id, full_name, legal_representative FROM users "
        "WHERE user_type_code = 'association' AND organization_id IS NULL"
    )).all():
        org_id = _new_organization(bind, "association", f"Asociación de {full_name}", None, rep)
        bind.execute(sa.text("UPDATE users SET organization_id = :org WHERE id = :id"), {"org": org_id, "id": user_id})

    # --- ECAs: every admin is an organization; the rest join only if that is unambiguous ---
    eca_orgs = []
    for user_id, full_name in bind.execute(sa.text(
        "SELECT id, full_name FROM users WHERE user_type_code = 'eca' AND role_code = 'eca_admin' ORDER BY created_at, id"
    )).all():
        org_id = _new_organization(bind, "eca", f"ECA de {full_name}", None, None)
        bind.execute(sa.text("UPDATE users SET organization_id = :org WHERE id = :id"), {"org": org_id, "id": user_id})
        eca_orgs.append(org_id)
    if len(eca_orgs) == 1:
        bind.execute(sa.text(
            "UPDATE users SET organization_id = :org WHERE user_type_code = 'eca' AND organization_id IS NULL"),
            {"org": eca_orgs[0]})
    orphans = bind.execute(sa.text(
        "SELECT count(*) FROM users WHERE user_type_code = 'eca' AND organization_id IS NULL")).scalar()
    if orphans:
        log.warning("0018: %s ECA account(s) left without an organization (%s ECA organizations exist); "
                    "assign them by hand", orphans, len(eca_orgs))


def downgrade() -> None:
    op.drop_index("ix_users_organization_id", table_name="users")
    op.drop_constraint("fk_users_organization", "users", type_="foreignkey")
    op.drop_column("users", "organization_id")
    op.drop_index("ix_organizations_status", table_name="organizations")
    op.drop_index("uq_organizations_type_tax_id", table_name="organizations")
    op.drop_table("organizations")
    op.execute("DROP TYPE organization_status")
    op.execute("DROP TYPE organization_type")
