"""weighings: the ECA receives material whoever brings it

An ECA must receive material regardless of the seller's affiliation (non-discrimination). A weighing can
now be for a registered recycler of any association or none, or for a person who is not in Somos R (a
private person, a recycler outside the platform), identified by name and document.

- weighings.recycler_id becomes optional; seller_name / seller_id_type / seller_id_number identify a person
  who is not registered. A CHECK requires one or the other.
- weighings.affiliation_status (linked | unlinked_association | independent) records how the seller relates
  to the ECA at the moment of weighing. Only `linked` weighings reach an association.

Existing weighings are classified from the data they have: linked when the recycler's association was
actively linked to the warehouse's ECA, otherwise unlinked_association or independent.

Downgrade removes the weighings of people who are not registered (and their purchases): they cannot exist
in the old schema.

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-30

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022"
down_revision: Union[str, Sequence[str], None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

VALUES = ("linked", "unlinked_association", "independent")


def upgrade() -> None:
    enum = postgresql.ENUM(*VALUES, name="affiliation_status", create_type=False)
    enum.create(op.get_bind(), checkfirst=True)

    op.add_column("weighings", sa.Column("affiliation_status", enum, nullable=True))
    op.execute("""
        UPDATE weighings w SET affiliation_status = (CASE
            WHEN u.organization_id IS NULL THEN 'independent'
            WHEN EXISTS (
                SELECT 1 FROM eca_association_links l JOIN warehouses wh ON wh.id = w.warehouse_id
                WHERE l.eca_id = wh.organization_id AND l.association_id = u.organization_id
                  AND l.status::text = 'active'
            ) THEN 'linked'
            ELSE 'unlinked_association' END)::affiliation_status
        FROM users u WHERE u.id = w.recycler_id
    """)
    op.alter_column("weighings", "affiliation_status", nullable=False)

    op.add_column("weighings", sa.Column("seller_name", sa.String(255), nullable=True))
    op.add_column("weighings", sa.Column("seller_id_type", sa.String(10), nullable=True))
    op.add_column("weighings", sa.Column("seller_id_number", sa.String(20), nullable=True))
    op.create_foreign_key("fk_weighings_seller_id_type", "weighings", "document_types", ["seller_id_type"], ["code"])
    op.alter_column("weighings", "recycler_id", nullable=True)
    op.create_check_constraint(
        "ck_weighings_has_seller", "weighings",
        "recycler_id IS NOT NULL OR (seller_name IS NOT NULL AND seller_id_type IS NOT NULL "
        "AND seller_id_number IS NOT NULL)")


def downgrade() -> None:
    op.execute("DELETE FROM transactions WHERE weighing_id IN (SELECT id FROM weighings WHERE recycler_id IS NULL)")
    op.execute("DELETE FROM weighings WHERE recycler_id IS NULL")
    op.drop_constraint("ck_weighings_has_seller", "weighings", type_="check")
    op.alter_column("weighings", "recycler_id", nullable=False)
    op.drop_constraint("fk_weighings_seller_id_type", "weighings", type_="foreignkey")
    op.drop_column("weighings", "seller_id_number")
    op.drop_column("weighings", "seller_id_type")
    op.drop_column("weighings", "seller_name")
    op.drop_column("weighings", "affiliation_status")
    op.execute("DROP TYPE affiliation_status")
