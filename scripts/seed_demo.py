"""Load demo data for trying the portal by hand: one Association, one ECA, an active link between them,
one account per staff role and a verified recycler. Development only: it refuses to run otherwise.

    docker compose exec app poetry run python scripts/seed_demo.py

Every demo account gets the same password (DEMO_PASSWORD below, or the DEMO_PASSWORD environment variable).
It is a throwaway value for a local database, not a credential for anything real. The script is idempotent:
accounts and organizations that already exist (by email / NIT) are left alone, so running it twice is safe.

Somos R's own account is not created here: use scripts/create_platform_admin.py (it enrolls TOTP in the
backoffice on first sign-in).
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# `_catalogs` is imported only to register the lookup tables the users' foreign keys point to.
from sqlalchemy import func, select, update  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.domains.catalogs import models as _catalogs  # noqa: E402,F401
from app.domains.inventory.models import Warehouse  # noqa: E402
from app.domains.organizations.enums import (  # noqa: E402
    LinkStatus,
    OrganizationStatus,
    OrganizationType,
)
from app.domains.organizations.models import (  # noqa: E402
    EcaAssociationLink,
    Organization,
)
from app.domains.users.enums import VerificationStatus  # noqa: E402
from app.domains.users.models import User  # noqa: E402

DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "CorrectHorse-9")

ASSOCIATION = {"legal_name": "Asociación Demo", "tax_id": "900111222-1", "city": "Bogotá"}
ECA = {"legal_name": "ECA Demo", "tax_id": "900333444-2", "city": "Bogotá"}

# (email, full name, document, user_type, role, organization key)
STAFF = [
    ("admin.asociacion@demo.test", "Aura Administradora", "2000000001", "association", "association_admin", "association"),
    ("operativo.asociacion@demo.test", "Omar Operativo", "2000000002", "association", "association_operator", "association"),
    ("rutas.asociacion@demo.test", "Rita Rutas", "2000000003", "association", "route_manager", "association"),
    ("admin.eca@demo.test", "Elena Administradora", "3000000001", "eca", "eca_admin", "eca"),
    ("bascula.eca@demo.test", "Bruno Báscula", "3000000002", "eca", "eca_operator", "eca"),
    ("bodega.eca@demo.test", "Beatriz Bodega", "3000000003", "eca", "eca_warehouse", "eca"),
]
RECYCLER = ("reciclador@demo.test", "Rafael Reciclador", "4000000001")


def get_or_create_organization(db, org_type: OrganizationType, data: dict) -> Organization:
    org = db.scalars(select(Organization).where(Organization.type == org_type, Organization.tax_id == data["tax_id"])).first()
    if org is None:
        org = Organization(type=org_type, status=OrganizationStatus.approved, approved_at=func.now(), **data)
        db.add(org)
        db.flush()
    return org


def ensure_user(db, email: str, **fields) -> bool:
    """Create the account unless the email exists. Returns True when it was created."""
    if db.scalar(select(User.id).where(func.lower(User.email) == email)):
        return False
    db.add(User(email=email, id_type="CC", password_hash=hash_password(DEMO_PASSWORD),
                email_verified_at=func.now(), **fields))
    return True


def main() -> int:
    if settings.app_env != "dev":
        print("Este script solo corre con APP_ENV=dev", file=sys.stderr)
        return 1

    with SessionLocal() as db:
        orgs = {
            "association": get_or_create_organization(db, OrganizationType.association, ASSOCIATION),
            "eca": get_or_create_organization(db, OrganizationType.eca, ECA),
        }

        created = 0
        for email, name, document, user_type, role, org_key in STAFF:
            created += ensure_user(db, email, full_name=name, id_number=document, user_type_code=user_type,
                                   role_code=role, organization_id=orgs[org_key].id)

        email, name, document = RECYCLER
        created += ensure_user(
            db, email, full_name=name, id_number=document, user_type_code="recycler",
            organization_id=orgs["association"].id, verification_status=VerificationStatus.verified,
            verified_at=func.now())

        link = db.scalars(select(EcaAssociationLink).where(
            EcaAssociationLink.eca_id == orgs["eca"].id,
            EcaAssociationLink.association_id == orgs["association"].id)).first()
        if link is None:
            db.add(EcaAssociationLink(eca_id=orgs["eca"].id, association_id=orgs["association"].id,
                                      status=LinkStatus.active, decided_at=func.now()))

        # The warehouses created by the migrations have no owner, so no ECA would see its inventory.
        claimed = db.execute(update(Warehouse).where(Warehouse.organization_id.is_(None))
                             .values(organization_id=orgs["eca"].id)).rowcount
        db.commit()

    print(f"Cuentas creadas: {created}. Bodegas asignadas a la ECA demo: {claimed}.")
    print(f"Contraseña de todas: {DEMO_PASSWORD}")
    for email, *_rest in STAFF:
        print(f"  {email}")
    print(f"  {RECYCLER[0]}  (reciclador verificado, sin pantalla en el portal)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
