"""Shrinkage (INV-01): material that is written off with a mandatory reason, never below zero."""
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.domains.audit.actions import Action
from app.domains.audit.models import AuditLog
from app.domains.inventory.models import InventoryItem, InventoryMovement
from tests import factories


def _losses(db):
    return db.scalars(select(InventoryMovement).where(InventoryMovement.movement_type == "loss")).all()


def _record(client_as, user, item, kg="10", reason="Humedad en el lote"):
    return client_as(user).post(f"/inventory/{item.id}/losses", json={"kg": kg, "reason": reason})


class TestRecordingALoss:
    def test_it_lowers_the_stock_and_writes_a_loss_movement_with_its_reason(self, client_as, eca_admin, warehouse, db):
        item = factories.stock(db, warehouse, kg="100")
        r = _record(client_as, eca_admin, item, kg="12.50")
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["movement_type"] == "loss" and body["kg_delta"] == "-12.50" and body["balance_after_kg"] == "87.50"
        assert body["reason"] == "Humedad en el lote" and body["actor_id"] == str(eca_admin.id)
        assert db.get(InventoryItem, item.id, populate_existing=True).stock_kg == Decimal("87.50")

    def test_the_loss_shows_up_in_the_ledger_with_its_reason(self, client_as, eca_admin, warehouse, db):
        item = factories.stock(db, warehouse, kg="100")
        _record(client_as, eca_admin, item, reason="Plástico contaminado")
        listed = client_as(eca_admin).get("/inventory/movements", params={"movement_type": "loss"}).json()
        assert [m["reason"] for m in listed["items"]] == ["Plástico contaminado"]

    def test_the_whole_stock_may_be_written_off(self, client_as, eca_admin, warehouse, db):
        item = factories.stock(db, warehouse, kg="40")
        assert _record(client_as, eca_admin, item, kg="40").json()["balance_after_kg"] == "0.00"

    def test_the_warehouse_keeper_may_too(self, client_as, eca_admin, warehouse, db):
        keeper = factories.make_user(db, "eca", role_code="eca_warehouse", organization_id=eca_admin.organization_id)
        item = factories.stock(db, warehouse)
        assert _record(client_as, keeper, item).status_code == 201


class TestWhatIsRefused:
    @pytest.mark.parametrize("reason", [None, "", "  ", "ab", "x" * 201])
    def test_the_reason_is_mandatory_and_bounded(self, client_as, eca_admin, warehouse, db, reason):
        item = factories.stock(db, warehouse)
        body = {"kg": "5"} if reason is None else {"kg": "5", "reason": reason}
        assert client_as(eca_admin).post(f"/inventory/{item.id}/losses", json=body).status_code == 422
        assert _losses(db) == []

    @pytest.mark.parametrize("kg", ["0", "-3", "1.234", "abc"])
    def test_the_amount_must_be_a_positive_number_with_two_decimals(self, client_as, eca_admin, warehouse, db, kg):
        item = factories.stock(db, warehouse)
        assert _record(client_as, eca_admin, item, kg=kg).status_code == 422
        assert _losses(db) == []

    def test_the_stock_never_goes_negative(self, client_as, eca_admin, warehouse, db):
        item = factories.stock(db, warehouse, kg="10")
        r = _record(client_as, eca_admin, item, kg="10.01")
        assert r.status_code == 400 and r.json()["code"] == "insufficient_stock"
        assert db.get(InventoryItem, item.id, populate_existing=True).stock_kg == Decimal("10")
        assert _losses(db) == []
        assert db.scalars(select(AuditLog).where(AuditLog.action == Action.INVENTORY_LOSS_RECORDED)).all() == []

    def test_an_unknown_item_is_a_404(self, client_as, eca_admin):
        r = client_as(eca_admin).post("/inventory/00000000-0000-0000-0000-000000000000/losses",
                                      json={"kg": "1", "reason": "Merma"})
        assert r.status_code == 404 and r.json()["code"] == "inventory_item_not_found"

    def test_only_the_staff_that_edits_the_inventory_may(self, client_as, eca_admin, association_admin, warehouse, db):
        operator = factories.make_user(db, "eca", role_code="eca_operator", organization_id=eca_admin.organization_id)
        item = factories.stock(db, warehouse)
        assert _record(client_as, operator, item).status_code == 403
        assert _record(client_as, association_admin, item).status_code == 403

    def test_another_ecas_item_does_not_exist_for_you(self, client_as, eca_admin, warehouse, db):
        item = factories.stock(db, warehouse, kg="100")
        other_eca = factories.make_organization(db, "eca", legal_name="Otra ECA")
        other_eca_admin = factories.make_user(db, "eca", role_code="eca_admin", organization_id=other_eca.id)
        r = _record(client_as, other_eca_admin, item)
        assert r.status_code == 404 and r.json()["code"] == "inventory_item_not_found"
        assert db.get(InventoryItem, item.id, populate_existing=True).stock_kg == Decimal("100")
        assert _losses(db) == []


class TestAudit:
    def test_it_records_the_material_and_the_amount_but_never_the_reason(self, client_as, eca_admin, warehouse, db):
        item = factories.stock(db, warehouse)
        _record(client_as, eca_admin, item, kg="3", reason="Se mojó con la lluvia")
        entry = db.scalars(select(AuditLog).where(AuditLog.action == Action.INVENTORY_LOSS_RECORDED)).one()
        assert entry.actor_id == eca_admin.id and entry.target_id == str(item.id)
        assert entry.details == {"material": "plastic", "kg": "3"}
