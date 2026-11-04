"""collection rejection reasons: the catalog an association rejects a pickup request with

- `rejection_reasons` is a new catalog (`code`, `label`, `is_correctable`, `is_active`), seeded with the three
  reasons the requester can correct and the three final ones.
- `collections` gains four nullable columns: `association_id` (the association that handles the request;
  nothing fills it yet), `rejection_reason_code`, `rejected_by` and `rejected_at`. Existing rows keep NULL.
- A check ties the state to its reason: a request is `rejected` exactly when it has a reason. No existing row
  can break it: nothing could reject a request before this revision.

Additive only. Downgrade drops the new columns and the catalog (and the rejections recorded after the upgrade).

Revision ID: 0033
Revises: 0032
Create Date: 2026-11-03

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0033"
down_revision: Union[str, Sequence[str], None] = "0032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

REASONS = [
    {"code": "incomplete_address", "label": "Dirección incompleta o no ubicable", "is_correctable": True},
    {"code": "invalid_contact", "label": "Datos de contacto inválidos", "is_correctable": True},
    {"code": "insufficient_description", "label": "Foto o descripción insuficiente", "is_correctable": True},
    {"code": "duplicate_request", "label": "Solicitud duplicada", "is_correctable": False},
    {"code": "outside_coverage", "label": "Fuera de la zona de cobertura", "is_correctable": False},
    {"code": "non_recoverable_material", "label": "Material no aprovechable", "is_correctable": False},
]


def upgrade() -> None:
    reasons = op.create_table(
        "rejection_reasons",
        sa.Column("code", sa.String(40), primary_key=True),
        sa.Column("label", sa.String(150), nullable=False),
        sa.Column("is_correctable", sa.Boolean, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
    )
    op.bulk_insert(reasons, [{**reason, "is_active": True} for reason in REASONS])

    op.add_column("collections", sa.Column(
        "association_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=True))
    op.add_column("collections", sa.Column(
        "rejection_reason_code", sa.String(40), sa.ForeignKey("rejection_reasons.code"), nullable=True))
    op.add_column("collections", sa.Column(
        "rejected_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True))
    op.add_column("collections", sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "ck_collections_rejected_has_reason", "collections",
        "(status = 'rejected') = (rejection_reason_code IS NOT NULL)")


def downgrade() -> None:
    op.drop_constraint("ck_collections_rejected_has_reason", "collections", type_="check")
    for column in ("rejected_at", "rejected_by", "rejection_reason_code", "association_id"):
        op.drop_column("collections", column)
    op.drop_table("rejection_reasons")
