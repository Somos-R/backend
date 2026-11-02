"""The integrity hash of a weighing: computed by the server, changes with any recorded fact, never with the status."""
import re
from decimal import Decimal

import pytest
from sqlalchemy import update

from app.domains.inventory.models import Warehouse
from app.domains.weighings import integrity
from app.domains.weighings.models import AffiliationStatus, Weighing
from app.main import app
from tests import factories

SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _register(client_as, eca_admin, recycler, warehouse, **overrides):
    payload = {"recycler_id": str(recycler.id), "material_code": "plastic", "warehouse_id": str(warehouse.id),
               "kg": "25.5", "price_per_kg": "400", **overrides}
    r = client_as(eca_admin).post("/weighings", json=payload)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _verify(client_as, user, weighing_id):
    return client_as(user).get(f"/weighings/{weighing_id}/verify")


class TestTheHashIsComputedByTheServer:
    def test_a_registered_weighing_has_a_sha256_that_verifies(self, client_as, eca_admin, recycler, warehouse, db):
        weighing_id = _register(client_as, eca_admin, recycler, warehouse)
        stored = db.get(Weighing, weighing_id)
        assert SHA256.match(stored.record_hash)
        body = _verify(client_as, eca_admin, weighing_id).json()
        assert body == {"id": weighing_id, "algorithm": "sha256", "record_hash": stored.record_hash, "valid": True}

    def test_the_client_cannot_send_it(self, client_as, eca_admin, recycler, warehouse, db):
        weighing_id = _register(client_as, eca_admin, recycler, warehouse, record_hash="f" * 64)
        assert db.get(Weighing, weighing_id).record_hash != "f" * 64
        assert _verify(client_as, eca_admin, weighing_id).json()["valid"] is True

    def test_the_hash_is_the_same_for_the_same_weighing(self, client_as, eca_admin, recycler, warehouse, db):
        stored = db.get(Weighing, _register(client_as, eca_admin, recycler, warehouse))
        assert integrity.compute_hash(stored) == integrity.compute_hash(stored) == stored.record_hash

    def test_it_survives_a_reload_from_the_database(self, client_as, eca_admin, recycler, warehouse, db):
        weighing_id = _register(client_as, eca_admin, recycler, warehouse)
        db.expire_all()
        assert _verify(client_as, eca_admin, weighing_id).json()["valid"] is True


class TestWhatIsStoredIsWhatWasSent:
    """A third decimal would be rounded by the database but not the same way by Python: it is refused instead."""

    @pytest.mark.parametrize("field, value", [("kg", "25.125"), ("price_per_kg", "400.005"), ("kg", "0.001")])
    def test_more_than_two_decimals_are_refused(self, client_as, eca_admin, recycler, warehouse, field, value):
        payload = {"recycler_id": str(recycler.id), "material_code": "plastic", "warehouse_id": str(warehouse.id),
                   "kg": "25.5", "price_per_kg": "400", field: value}
        assert client_as(eca_admin).post("/weighings", json=payload).status_code == 422

    @pytest.mark.parametrize("kg, price", [("25.12", "400.05"), ("25.13", "400.5"), ("1000", "1")])
    def test_what_is_accepted_verifies_after_a_reload(self, client_as, eca_admin, recycler, warehouse, db, kg, price):
        weighing_id = _register(client_as, eca_admin, recycler, warehouse, kg=kg, price_per_kg=price)
        db.expire_all()
        assert db.get(Weighing, weighing_id).kg == Decimal(kg)
        assert _verify(client_as, eca_admin, weighing_id).json()["valid"] is True


class TestAnyChangeIsDetected:
    @pytest.mark.parametrize("column, value", [
        ("kg", Decimal("25.51")),
        ("price_per_kg", Decimal("401")),
        ("material_code", "glass"),
        ("seller_name", "Otra Persona"),
        ("seller_id_number", "999"),
    ])
    def test_changing_a_recorded_fact_makes_it_invalid(self, client_as, eca_admin, recycler, warehouse, db, column, value):
        weighing_id = _register(client_as, eca_admin, recycler, warehouse)
        db.execute(update(Weighing).where(Weighing.id == weighing_id).values({column: value}))
        db.commit()
        assert _verify(client_as, eca_admin, weighing_id).json()["valid"] is False

    @pytest.mark.parametrize("fact", ["occurred_at", "warehouse_id", "affiliation_status", "recycler_id"])
    def test_changing_when_where_or_who_makes_it_invalid(self, client_as, eca_admin, recycler, warehouse, db, fact):
        weighing_id = _register(client_as, eca_admin, recycler, warehouse)
        stored = db.get(Weighing, weighing_id)
        factories.own_all_warehouses(db)  # still readable by the same ECA after the change
        other_warehouse = next(w for w in db.query(Warehouse).all() if w.id != warehouse.id)
        changed = {
            "occurred_at": stored.occurred_at.replace(year=2020),
            "warehouse_id": other_warehouse.id,
            "affiliation_status": AffiliationStatus.independent,
            "recycler_id": factories.make_user(db, "recycler").id,
        }[fact]
        db.execute(update(Weighing).where(Weighing.id == weighing_id).values({fact: changed}))
        db.commit()
        assert _verify(client_as, eca_admin, weighing_id).json()["valid"] is False

    def test_a_walk_in_seller_is_covered_too(self, client_as, eca_admin, warehouse, db):
        payload = {"material_code": "plastic", "warehouse_id": str(warehouse.id), "kg": "10", "price_per_kg": "400",
                   "seller": {"full_name": "Ana Vendedora", "id_type": "CC", "id_number": "52123456"}}
        weighing_id = client_as(eca_admin).post("/weighings", json=payload).json()["id"]
        assert _verify(client_as, eca_admin, weighing_id).json()["valid"] is True
        db.execute(update(Weighing).where(Weighing.id == weighing_id).values(seller_id_number="52123457"))
        db.commit()
        assert _verify(client_as, eca_admin, weighing_id).json()["valid"] is False


class TestTheStatusIsNotPartOfTheHash:
    def test_validating_and_paying_keep_it_valid(self, client_as, eca_admin, recycler, warehouse, db):
        weighing_id = _register(client_as, eca_admin, recycler, warehouse)
        before = db.get(Weighing, weighing_id).record_hash
        assert client_as(eca_admin).patch(f"/weighings/{weighing_id}/status", json={"status": "validated"}).status_code == 200
        assert client_as(eca_admin).patch(f"/weighings/{weighing_id}/status", json={"status": "paid"}).status_code == 200
        assert _verify(client_as, eca_admin, weighing_id).json()["valid"] is True
        assert db.get(Weighing, weighing_id, populate_existing=True).record_hash == before


class TestOlderWeighings:
    def test_one_registered_before_hashing_verifies_as_unknown(self, client_as, eca_admin, recycler, warehouse, db):
        weighing_id = _register(client_as, eca_admin, recycler, warehouse)
        db.execute(update(Weighing).where(Weighing.id == weighing_id).values(record_hash=None))
        db.commit()
        body = _verify(client_as, eca_admin, weighing_id).json()
        assert body["valid"] is None and body["record_hash"] is None


class TestWhoMayVerify:
    def test_someone_who_cannot_read_the_weighing_gets_a_404(self, client_as, eca_admin, recycler, warehouse, db):
        weighing_id = _register(client_as, eca_admin, recycler, warehouse)
        other_eca = factories.make_organization(db, "eca", legal_name="Otra ECA")
        outsider = factories.make_user(db, "eca", role_code="eca_admin", organization_id=other_eca.id)
        r = _verify(client_as, outsider, weighing_id)
        assert r.status_code == 404 and r.json()["code"] == "weighing_not_found"

    def test_the_recycler_may_verify_their_own(self, client_as, eca_admin, recycler, warehouse):
        weighing_id = _register(client_as, eca_admin, recycler, warehouse)
        assert _verify(client_as, recycler, weighing_id).json()["valid"] is True

    def test_it_requires_a_session(self, client_as, eca_admin, recycler, warehouse):
        from fastapi.testclient import TestClient
        weighing_id = _register(client_as, eca_admin, recycler, warehouse)
        assert TestClient(app).get(f"/weighings/{weighing_id}/verify").status_code in (401, 403)


def test_no_endpoint_edits_a_registered_weighing():
    """The only way to change a weighing is its status; the recorded facts have no PUT, PATCH or DELETE."""
    changing = {
        (path, method.upper())
        for path, operations in app.openapi()["paths"].items() if path.startswith("/weighings/")
        for method in operations if method in {"put", "patch", "delete"}
    }
    assert changing == {("/weighings/{weighing_id}/status", "PATCH")}
