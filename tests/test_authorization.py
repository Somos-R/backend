"""Authorization guardrail (task 1.6): endpoint x actor matrix plus ownership rules.

The expectations below are written out literally and independently of
app/core/permissions.py on purpose: they are the spec (docs/matriz-permisos.md),
and this file must fail if the code drifts from it.
"""
from decimal import Decimal

import pytest

from app.domains.transactions import service as tx_service
from app.domains.users.enums import VerificationStatus
from app.domains.users.models import User
from app.domains.weighings.models import Weighing
from tests import factories
from tests.factories import ACTORS, make_actor

ALL = set(ACTORS)
ECA_STAFF = {"eca_admin", "eca_operator", "eca_warehouse"}
ASSOC_STAFF = {"association_admin", "association_operator", "route_manager"}
STAFF = ECA_STAFF | ASSOC_STAFF
STAFF_NO_ROUTES = STAFF - {"route_manager"}
NOBODY: set[str] = set()


@pytest.fixture
def world(db, warehouse):
    """Concrete resources every matrix case can point at."""
    other_recycler = factories.make_user(db, "recycler")
    other_citizen = factories.make_user(db, "citizen")
    pending_recycler = factories.make_user(
        db, "recycler", verification_status=VerificationStatus.pending, password_hash="")
    item = factories.stock(db, warehouse, kg="100")

    weighing = Weighing(
        recycler_id=other_recycler.id, material_code="plastic", warehouse_id=warehouse.id,
        kg=Decimal("10"), price_per_kg=Decimal("100"))
    db.add(weighing)
    admin = factories.make_actor(db, "eca_admin")
    sale = tx_service.create_sale(
        db, "plastic", warehouse.id, Decimal("5"), Decimal("100"), created_by=admin.id)
    db.commit()
    return {
        "recycler": other_recycler, "citizen": other_citizen, "pending": pending_recycler,
        "item": item, "weighing": weighing, "sale": sale, "warehouse": warehouse,
    }


def _weighing_body(w):
    return {"recycler_id": str(w["recycler"].id), "material_code": "plastic",
            "warehouse_id": str(w["warehouse"].id), "kg": "5", "price_per_kg": "100"}


# (id, method, path, body, kinds allowed past the authorization layer)
MATRIX = [
    ("users:list", "GET", lambda w: "/users", None, STAFF),
    ("users:get-recycler", "GET", lambda w: f"/users/{w['recycler'].id}", None, STAFF),
    ("users:get-citizen", "GET", lambda w: f"/users/{w['citizen'].id}", None, {"association_admin"}),
    ("users:patch-other-citizen", "PATCH", lambda w: f"/users/{w['citizen'].id}",
     lambda w: {"phone": "3000000000"}, NOBODY),
    ("users:verify", "PATCH", lambda w: f"/users/{w['pending'].id}/verification-status",
     lambda w: {"status": "verified"}, {"association_admin", "association_operator"}),

    ("weighings:list", "GET", lambda w: "/weighings", None, STAFF_NO_ROUTES | {"recycler"}),
    ("weighings:stats", "GET", lambda w: "/weighings/stats", None, STAFF_NO_ROUTES),
    # A recycler asking for another recycler's weighing passes authorization but gets a
    # 404 (not 403) so the id is not revealed; see TestRecyclerOwnership.
    ("weighings:get", "GET", lambda w: f"/weighings/{w['weighing'].id}", None,
     STAFF_NO_ROUTES | {"recycler"}),
    ("weighings:create", "POST", lambda w: "/weighings", _weighing_body,
     {"eca_admin", "eca_operator"}),
    ("weighings:validate", "PATCH", lambda w: f"/weighings/{w['weighing'].id}/status",
     lambda w: {"status": "validated"},
     {"eca_admin", "eca_operator", "association_admin", "association_operator"}),
    ("weighings:pay", "PATCH", lambda w: f"/weighings/{w['weighing'].id}/status",
     lambda w: {"status": "paid"}, {"eca_admin", "association_admin"}),

    ("inventory:list", "GET", lambda w: "/inventory", None, STAFF_NO_ROUTES),
    ("inventory:stats", "GET", lambda w: "/inventory/stats", None, STAFF_NO_ROUTES),
    ("inventory:materials", "GET", lambda w: "/inventory/materials", None, STAFF_NO_ROUTES),
    ("inventory:warehouses", "GET", lambda w: "/inventory/warehouses", None, STAFF_NO_ROUTES),
    ("inventory:get", "GET", lambda w: f"/inventory/{w['item'].id}", None, STAFF_NO_ROUTES),
    ("inventory:patch", "PATCH", lambda w: f"/inventory/{w['item'].id}",
     lambda w: {"price_per_kg": "1"}, {"eca_admin", "eca_warehouse"}),

    ("transactions:list", "GET", lambda w: "/transactions", None, ECA_STAFF | {"association_admin"}),
    ("transactions:stats", "GET", lambda w: "/transactions/stats", None,
     ECA_STAFF | {"association_admin"}),
    ("transactions:get", "GET", lambda w: f"/transactions/{w['sale'].id}", None,
     ECA_STAFF | {"association_admin"}),
    ("transactions:create-sale", "POST", lambda w: "/transactions",
     lambda w: {"material_code": "plastic", "warehouse_id": str(w["warehouse"].id),
                "kg": "1", "price_per_kg": "100"}, {"eca_admin", "eca_warehouse"}),
    ("transactions:cancel", "PATCH", lambda w: f"/transactions/{w['sale'].id}/status",
     lambda w: {"status": "cancelled"}, {"eca_admin", "eca_warehouse"}),
    ("transactions:pay", "PATCH", lambda w: f"/transactions/{w['sale'].id}/status",
     lambda w: {"status": "paid"}, {"eca_admin", "association_admin"}),

    ("audit:list", "GET", lambda w: "/audit-log", None, {"association_admin"}),
]


@pytest.mark.parametrize("kind", sorted(ALL))
@pytest.mark.parametrize("case", MATRIX, ids=[m[0] for m in MATRIX])
def test_authorization_matrix(case, kind, client_as, db, world):
    _, method, path, body, allowed = case
    actor = make_actor(db, kind)
    response = client_as(actor).request(
        method, path(world), json=body(world) if body else None)

    if kind in allowed:
        assert response.status_code != 403, (
            f"{kind} should be allowed on {method} {path(world)}: {response.text}")
    else:
        assert response.status_code == 403, (
            f"{kind} must be denied on {method} {path(world)}, got {response.status_code}")


@pytest.mark.parametrize("case", MATRIX, ids=[m[0] for m in MATRIX])
def test_every_protected_endpoint_rejects_anonymous(case, client, world):
    _, method, path, body, _ = case
    response = client.request(method, path(world), json=body(world) if body else None)
    assert response.status_code in (401, 403)


# --- Role must match the actor type (defense in depth) -----------------------

def test_role_of_another_actor_type_grants_nothing(client_as, db):
    impostor = factories.make_user(db, "citizen", role_code="eca_admin")
    assert client_as(impostor).get("/inventory").status_code == 403
    assert client_as(impostor).get("/users").status_code == 403


# --- Users: ownership and role assignment -------------------------------------

class TestSelfService:
    def test_user_can_read_and_edit_own_profile(self, client_as, citizen):
        c = client_as(citizen)
        assert c.get(f"/users/{citizen.id}").status_code == 200
        r = c.patch(f"/users/{citizen.id}", json={"phone": "3119999999"})
        assert r.status_code == 200 and r.json()["phone"] == "3119999999"

    def test_user_cannot_read_or_edit_someone_else(self, client_as, citizen, db):
        other = factories.make_user(db, "citizen")
        c = client_as(citizen)
        assert c.get(f"/users/{other.id}").status_code == 403
        assert c.patch(f"/users/{other.id}", json={"phone": "0"}).status_code == 403

    @pytest.mark.parametrize("field,value", [
        ("role_code", "eca_admin"), ("permissions", {"all": True}),
        ("association_id", "00000000-0000-0000-0000-000000000001"), ("employee_code", "X-1"),
    ])
    def test_user_cannot_change_privileged_fields_on_self(self, client_as, citizen, db, field, value):
        r = client_as(citizen).patch(f"/users/{citizen.id}", json={field: value})
        assert r.status_code == 403
        db.expire_all()
        assert db.get(User, citizen.id).role_code is None

    def test_org_admin_cannot_promote_themselves(self, client_as, eca_admin):
        r = client_as(eca_admin).patch(f"/users/{eca_admin.id}", json={"role_code": "eca_admin"})
        assert r.status_code == 403


class TestRoleAssignment:
    def test_eca_admin_can_assign_eca_role_to_eca_user(self, client_as, eca_admin, db):
        staff = factories.make_user(db, "eca")
        r = client_as(eca_admin).patch(f"/users/{staff.id}", json={"role_code": "eca_warehouse"})
        assert r.status_code == 200
        assert r.json()["role_code"] == "eca_warehouse"

    def test_eca_admin_cannot_touch_association_users(self, client_as, eca_admin, db):
        staff = factories.make_user(db, "association")
        r = client_as(eca_admin).patch(f"/users/{staff.id}", json={"role_code": "association_admin"})
        assert r.status_code == 403

    def test_role_must_fit_the_target_actor_type(self, client_as, eca_admin, db):
        staff = factories.make_user(db, "eca")
        r = client_as(eca_admin).patch(f"/users/{staff.id}", json={"role_code": "association_admin"})
        assert r.status_code == 422

    def test_unknown_role_is_rejected(self, client_as, eca_admin, db):
        staff = factories.make_user(db, "eca")
        r = client_as(eca_admin).patch(f"/users/{staff.id}", json={"role_code": "superadmin"})
        assert r.status_code == 422

    def test_association_admin_can_manage_association_staff(self, client_as, association_admin, db):
        staff = factories.make_user(db, "association")
        r = client_as(association_admin).patch(
            f"/users/{staff.id}", json={"role_code": "route_manager"})
        assert r.status_code == 200

    def test_non_admin_staff_cannot_assign_roles(self, client_as, db):
        operator = make_actor(db, "eca_operator")
        staff = factories.make_user(db, "eca")
        r = client_as(operator).patch(f"/users/{staff.id}", json={"role_code": "eca_admin"})
        assert r.status_code == 403


class TestRegisterWithRole:
    @staticmethod
    def _payload(**overrides):
        payload = {
            "user_type_code": "eca", "email": "nuevo@eca.com", "password": "Segura12345",
            "full_name": "Nuevo Operador", "id_type": "CC", "id_number": "70707070",
        }
        payload.update(overrides)
        return payload

    def test_anonymous_cannot_register_a_role(self, client):
        r = client.post("/auth/register", json=self._payload(role_code="eca_admin"))
        assert r.status_code == 403

    def test_anonymous_can_still_register_without_a_role(self, client):
        r = client.post("/auth/register", json=self._payload())
        assert r.status_code == 201
        assert r.json()["role_code"] is None

    def test_citizen_cannot_register_a_role(self, client_as, citizen):
        r = client_as(citizen).post("/auth/register", json=self._payload(role_code="eca_admin"))
        assert r.status_code == 403

    def test_eca_admin_can_create_eca_staff(self, client_as, eca_admin):
        r = client_as(eca_admin).post("/auth/register", json=self._payload(role_code="eca_operator"))
        assert r.status_code == 201
        assert r.json()["role_code"] == "eca_operator"

    def test_eca_admin_cannot_create_association_staff(self, client_as, eca_admin):
        r = client_as(eca_admin).post("/auth/register", json=self._payload(
            user_type_code="association", role_code="association_admin",
            association_nit="900111222-3", legal_representative="Rep"))
        assert r.status_code == 403

    def test_invalid_role_is_rejected_for_an_admin(self, client_as, eca_admin):
        r = client_as(eca_admin).post("/auth/register", json=self._payload(role_code="superadmin"))
        assert r.status_code == 422


# --- Directory scoping ----------------------------------------------------------

class TestDirectoryScope:
    def test_operators_only_see_recyclers(self, client_as, db):
        operator = make_actor(db, "eca_operator")
        factories.make_user(db, "citizen")
        factories.make_user(db, "recycler")
        r = client_as(operator).get("/users")
        assert r.status_code == 200
        assert {u["user_type_code"] for u in r.json()["items"]} == {"recycler"}

    def test_operators_cannot_ask_for_other_types(self, client_as, db):
        operator = make_actor(db, "association_operator")
        assert client_as(operator).get("/users?user_type_code=citizen").status_code == 403

    def test_eca_admin_sees_eca_and_recyclers_only(self, client_as, eca_admin, db):
        factories.make_user(db, "citizen")
        r = client_as(eca_admin).get("/users?limit=500")
        assert {u["user_type_code"] for u in r.json()["items"]} <= {"eca", "recycler"}

    def test_association_admin_sees_everyone(self, client_as, association_admin, db):
        factories.make_user(db, "citizen")
        r = client_as(association_admin).get("/users?user_type_code=citizen")
        assert r.status_code == 200 and r.json()["items"]


# --- Recycler ownership ----------------------------------------------------------

class TestRecyclerOwnership:
    @pytest.fixture
    def two_weighings(self, client_as, eca_admin, recycler, warehouse, db):
        other = factories.make_user(db, "recycler")
        admin = client_as(eca_admin)
        ids = {}
        for name, owner in (("mine", recycler), ("theirs", other)):
            ids[name] = admin.post("/weighings", json={
                "recycler_id": str(owner.id), "material_code": "plastic",
                "warehouse_id": str(warehouse.id), "kg": "10", "price_per_kg": "100",
            }).json()["id"]
        ids["other_recycler"] = other
        return ids

    def test_recycler_lists_only_own_weighings(self, client_as, recycler, two_weighings):
        body = client_as(recycler).get("/weighings").json()
        assert {w["id"] for w in body["items"]} == {two_weighings["mine"]}

    def test_recycler_cannot_filter_by_someone_else(self, client_as, recycler, two_weighings):
        other = two_weighings["other_recycler"]
        assert client_as(recycler).get(f"/weighings?recycler_id={other.id}").status_code == 403

    def test_recycler_reads_own_weighing_but_not_others(self, client_as, recycler, two_weighings):
        c = client_as(recycler)
        assert c.get(f"/weighings/{two_weighings['mine']}").status_code == 200
        assert c.get(f"/weighings/{two_weighings['theirs']}").status_code == 404
