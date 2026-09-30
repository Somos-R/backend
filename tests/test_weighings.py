"""Characterization tests for /weighings, including the validation side effects."""
from decimal import Decimal

import pytest

from app.domains.audit.models import AuditLog
from app.domains.inventory.models import InventoryItem
from app.domains.transactions.models import (
    Transaction,
    TransactionStatus,
    TransactionType,
)
from app.domains.users.enums import VerificationStatus
from app.domains.weighings.models import Weighing
from tests import factories

MISSING_ID = "00000000-0000-0000-0000-000000000000"


def _payload(recycler, warehouse, **overrides):
    payload = {
        "recycler_id": str(recycler.id),
        "material_code": "plastic",
        "warehouse_id": str(warehouse.id),
        "kg": "25.5",
        "price_per_kg": "400",
    }
    payload.update(overrides)
    return payload


def _create(client, recycler, warehouse, **overrides):
    r = client.post("/weighings", json=_payload(recycler, warehouse, **overrides))
    assert r.status_code == 201, r.text
    return r.json()


class TestCreate:
    def test_creates_pending_weighing(self, client_as, eca_admin, recycler, warehouse):
        body = _create(client_as(eca_admin), recycler, warehouse)
        assert body["status"] == "pending_validation"
        assert Decimal(body["total_value"]) == Decimal("10200")
        assert body["recycler"]["id"] == str(recycler.id)

    def test_recycler_must_exist_and_be_a_recycler(self, client_as, eca_admin, citizen, warehouse):
        r = client_as(eca_admin).post("/weighings", json=_payload(citizen, warehouse))
        assert r.status_code == 404

    def test_unknown_material(self, client_as, eca_admin, recycler, warehouse):
        r = client_as(eca_admin).post(
            "/weighings", json=_payload(recycler, warehouse, material_code="unobtanium"))
        assert r.status_code == 404

    def test_unknown_warehouse(self, client_as, eca_admin, recycler, warehouse):
        r = client_as(eca_admin).post(
            "/weighings", json=_payload(recycler, warehouse, warehouse_id=MISSING_ID))
        assert r.status_code == 404

    def test_non_positive_values_are_rejected(self, client_as, eca_admin, recycler, warehouse):
        for field in ("kg", "price_per_kg"):
            for bad in ("0", "-5"):
                r = client_as(eca_admin).post(
                    "/weighings", json=_payload(recycler, warehouse, **{field: bad}))
                assert r.status_code == 422


class TestReadEndpoints:
    def test_list_and_filters(self, client_as, eca_admin, recycler, warehouse, db):
        c = client_as(eca_admin)
        _create(c, recycler, warehouse, material_code="plastic")
        _create(c, recycler, warehouse, material_code="glass")

        assert c.get("/weighings").json()["total"] == 2
        r = c.get("/weighings?material_code=glass")
        assert [w["material_code"] for w in r.json()["items"]] == ["glass"]
        assert c.get("/weighings?status=validated").json()["total"] == 0
        other = factories.make_user(db, "recycler")
        assert c.get(f"/weighings?recycler_id={other.id}").json()["total"] == 0

    def test_filter_by_status_uses_the_new_codes(self, client_as, eca_admin, recycler, warehouse):
        c = client_as(eca_admin)
        first = _create(c, recycler, warehouse)
        _create(c, recycler, warehouse)
        c.patch(f"/weighings/{first['id']}/status", json={"status": "validated"})
        assert c.get("/weighings?status=validated").json()["total"] == 1
        assert c.get("/weighings?status=pending_validation").json()["total"] == 1

    def test_an_unknown_status_filter_is_a_422_not_a_500(self, client_as, eca_admin):
        assert client_as(eca_admin).get("/weighings?status=inventado").status_code == 422

    def test_the_old_spanish_status_values_are_gone(self, client_as, eca_admin):
        c = client_as(eca_admin)
        for old in ("pendiente", "validado", "rechazado", "pagado"):
            assert c.get(f"/weighings?status={old}").status_code == 422

    def test_get_one(self, client_as, eca_admin, recycler, warehouse):
        c = client_as(eca_admin)
        created = _create(c, recycler, warehouse)
        assert c.get(f"/weighings/{created['id']}").json()["id"] == created["id"]

    def test_get_not_found(self, client_as, eca_admin):
        assert client_as(eca_admin).get(f"/weighings/{MISSING_ID}").status_code == 404

    def test_stats(self, client_as, eca_admin, recycler, warehouse):
        c = client_as(eca_admin)
        _create(c, recycler, warehouse, kg="10")
        _create(c, recycler, warehouse, kg="5", material_code="glass")
        body = c.get("/weighings/stats").json()
        assert body["total_weighings_month"] == 2
        assert Decimal(body["total_kg_month"]) == Decimal("15")
        assert body["pending_count"] == 2
        assert {m["material"] for m in body["by_material"]} == {"plastic", "glass"}


class TestStatusTransitions:
    def test_validate_adds_stock_and_creates_purchase(
        self, client_as, eca_admin, recycler, warehouse, db
    ):
        c = client_as(eca_admin)
        w = _create(c, recycler, warehouse, kg="30", price_per_kg="400")

        r = c.patch(f"/weighings/{w['id']}/status", json={"status": "validated"})
        assert r.status_code == 200
        assert r.json()["status"] == "validated"
        assert r.json()["validated_by"] == str(eca_admin.id)

        item = db.query(InventoryItem).filter_by(
            material_code="plastic", warehouse_id=warehouse.id).one()
        assert item.stock_kg == Decimal("30")
        assert item.price_per_kg == Decimal("400")

        tx = db.query(Transaction).filter_by(weighing_id=w["id"]).one()
        assert tx.type == TransactionType.purchase
        assert tx.status == TransactionStatus.pending
        assert tx.recycler_id == recycler.id

    def test_validate_twice_is_rejected(self, client_as, eca_admin, recycler, warehouse):
        c = client_as(eca_admin)
        w = _create(c, recycler, warehouse)
        c.patch(f"/weighings/{w['id']}/status", json={"status": "validated"})
        r = c.patch(f"/weighings/{w['id']}/status", json={"status": "validated"})
        assert r.status_code == 400

    def test_reject_requires_reason(self, client_as, eca_admin, recycler, warehouse):
        c = client_as(eca_admin)
        w = _create(c, recycler, warehouse)
        r = c.patch(f"/weighings/{w['id']}/status", json={"status": "rejected"})
        assert r.status_code == 400

    def test_reject_with_reason_does_not_touch_stock(
        self, client_as, eca_admin, recycler, warehouse, db
    ):
        c = client_as(eca_admin)
        w = _create(c, recycler, warehouse)
        r = c.patch(
            f"/weighings/{w['id']}/status",
            json={"status": "rejected", "rejection_reason": "Material contaminado"})
        assert r.status_code == 200
        assert r.json()["status"] == "rejected"
        assert db.query(InventoryItem).count() == 0

    def test_pay_requires_validated(self, client_as, eca_admin, recycler, warehouse):
        c = client_as(eca_admin)
        w = _create(c, recycler, warehouse)
        assert c.patch(f"/weighings/{w['id']}/status", json={"status": "paid"}).status_code == 400

    def test_full_happy_path_to_paid(self, client_as, eca_admin, recycler, warehouse):
        c = client_as(eca_admin)
        w = _create(c, recycler, warehouse)
        c.patch(f"/weighings/{w['id']}/status", json={"status": "validated"})
        r = c.patch(f"/weighings/{w['id']}/status", json={"status": "paid"})
        assert r.status_code == 200
        assert r.json()["status"] == "paid"

    def test_cannot_go_back_to_pending(self, client_as, eca_admin, recycler, warehouse):
        c = client_as(eca_admin)
        w = _create(c, recycler, warehouse)
        r = c.patch(f"/weighings/{w['id']}/status", json={"status": "pending_validation"})
        assert r.status_code == 400

    def test_not_found(self, client_as, eca_admin):
        r = client_as(eca_admin).patch(
            f"/weighings/{MISSING_ID}/status", json={"status": "validated"})
        assert r.status_code == 404


class TestTheEcaReceivesWhoeverBringsIt:
    """An ECA receives material whatever the seller's affiliation or verification (non-discrimination).

    This replaces the earlier rule that a weighing needed a verified recycler. Only an account that Somos R
    itself has deactivated is refused. What changes with the seller is where the weighing goes afterwards
    (see test_eca_receives_any_seller.py).
    """

    @pytest.mark.parametrize("state", [VerificationStatus.pending, VerificationStatus.rejected])
    def test_an_unverified_recycler_can_be_weighed(self, client_as, eca_admin, warehouse, db, state):
        recycler = factories.make_user(db, "recycler", verification_status=state)
        r = client_as(eca_admin).post("/weighings", json=_payload(recycler, warehouse))
        assert r.status_code == 201, r.text
        assert r.json()["affiliation_status"] == "unlinked_association"  # not verified: not through the link

    def test_a_deactivated_account_is_still_refused(self, client_as, eca_admin, warehouse, db):
        recycler = factories.make_user(db, "recycler", is_active=False)
        r = client_as(eca_admin).post("/weighings", json=_payload(recycler, warehouse))
        assert r.status_code == 400 and r.json()["code"] == "recycler_inactive"

    def test_a_refused_weighing_leaves_no_row_and_no_audit_entry(self, client_as, eca_admin, warehouse, db):
        recycler = factories.make_user(db, "recycler", is_active=False)
        client_as(eca_admin).post("/weighings", json=_payload(recycler, warehouse))
        assert db.query(Weighing).count() == 0
        assert db.query(AuditLog).filter(AuditLog.action == "weighing.created").count() == 0

    def test_a_verified_recycler_still_works(self, client_as, eca_admin, recycler, warehouse):
        r = client_as(eca_admin).post("/weighings", json=_payload(recycler, warehouse))
        assert r.status_code == 201 and r.json()["affiliation_status"] == "linked"

    def test_a_user_who_is_not_a_recycler_is_still_a_404(self, client_as, eca_admin, citizen, warehouse):
        assert client_as(eca_admin).post("/weighings", json=_payload(citizen, warehouse)).status_code == 404

    def test_losing_verification_before_validation_does_not_block_it(self, client_as, eca_admin, recycler, warehouse, db):
        c = client_as(eca_admin)
        weighing = _create(c, recycler, warehouse)
        recycler.verification_status = VerificationStatus.rejected
        db.commit()
        r = c.patch(f"/weighings/{weighing['id']}/status", json={"status": "validated"})
        assert r.status_code == 200

    def test_deactivating_the_account_before_validation_blocks_it(self, client_as, eca_admin, recycler, warehouse, db):
        c = client_as(eca_admin)
        weighing = _create(c, recycler, warehouse)
        recycler.is_active = False
        db.commit()
        r = c.patch(f"/weighings/{weighing['id']}/status", json={"status": "validated"})
        assert r.status_code == 400 and r.json()["code"] == "recycler_inactive"
        # nothing moved: still pending, no stock, no purchase owed
        db.expire_all()
        assert db.get(Weighing, weighing["id"]).status.value == "pending_validation"
        assert db.query(InventoryItem).count() == 0
        assert c.get("/transactions?type=purchase").json()["total"] == 0

    def test_a_weighing_can_still_be_rejected_after_the_recycler_lost_verification(
        self, client_as, eca_admin, recycler, warehouse, db
    ):
        c = client_as(eca_admin)
        weighing = _create(c, recycler, warehouse)
        recycler.verification_status = VerificationStatus.rejected
        db.commit()
        r = c.patch(f"/weighings/{weighing['id']}/status",
                    json={"status": "rejected", "rejection_reason": "Material contaminado"})
        assert r.status_code == 200 and r.json()["status"] == "rejected"
