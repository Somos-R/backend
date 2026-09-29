"""Builders for test data. Keep them thin: one call, sensible defaults."""
import itertools
import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.security import create_access_token, hash_password
from app.domains.inventory import service as inventory_service
from app.domains.inventory.models import InventoryItem, Warehouse
from app.domains.users.enums import VerificationStatus
from app.domains.users.models import User

DEFAULT_PASSWORD = "Segura12345"

_counter = itertools.count(1)

# bcrypt is deliberately slow; hash the shared test password once, not per user.
_PASSWORD_HASH = hash_password(DEFAULT_PASSWORD)

# Required extra columns per actor type (mirrors auth/schemas.py).
_TYPE_DEFAULTS: dict[str, dict] = {
    "citizen": {},
    "building": {"building_name": "Conjunto Test", "num_units": 10},
    "recycler": {},
    "eca": {},
    "association": {"association_nit": "900123456-7", "legal_representative": "Rep Test"},
    "b2b_client": {"company_name": "Empresa Test", "tax_id": None},
}


def make_user(db: Session, user_type: str = "citizen", **overrides) -> User:
    n = next(_counter)
    data = {
        "email": f"user{n}-{uuid.uuid4().hex[:6]}@test.com",
        "password_hash": _PASSWORD_HASH,
        "full_name": f"Test User {n}",
        "phone": "3001234567",
        "id_type": "CC",
        "id_number": f"{10_000_000 + n}{uuid.uuid4().int % 1000:03d}",
        "user_type_code": user_type,
        **_TYPE_DEFAULTS[user_type],
    }
    if user_type == "b2b_client":
        data["tax_id"] = f"9{n:08d}-1"
    if user_type == "recycler":
        data["verification_status"] = VerificationStatus.verified
    data.update(overrides)

    user = User(**data)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def auth_headers(user: User) -> dict[str, str]:
    token = create_access_token(
        {"sub": str(user.id), "user_type": user.user_type_code, "role": user.role_code}
    )
    return {"Authorization": f"Bearer {token}"}


def first_warehouse(db: Session) -> Warehouse:
    return db.query(Warehouse).order_by(Warehouse.name).first()  # type: ignore[return-value]


def stock(
    db: Session,
    warehouse: Warehouse,
    material_code: str = "plastico",
    kg: str = "100",
    precio_kg: str = "500",
) -> InventoryItem:
    item = inventory_service.add_stock(
        db, material_code, warehouse.id, Decimal(kg), Decimal(precio_kg)
    )
    db.commit()
    return item


# Every actor kind exercised by the authorization matrix: kind -> (user_type, role_code).
ACTORS: dict[str, tuple[str, str | None]] = {
    "citizen": ("citizen", None),
    "building": ("building", None),
    "b2b_client": ("b2b_client", None),
    "recycler": ("recycler", None),
    "eca_admin": ("eca", "eca_admin"),
    "eca_operator": ("eca", "eca_operator"),
    "eca_warehouse": ("eca", "eca_warehouse"),
    "association_admin": ("association", "association_admin"),
    "association_operator": ("association", "association_operator"),
    "route_manager": ("association", "route_manager"),
}


def make_actor(db: Session, kind: str, **overrides) -> User:
    user_type, role_code = ACTORS[kind]
    return make_user(db, user_type, role_code=role_code, **overrides)
