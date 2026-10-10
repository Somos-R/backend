"""The ledger of the stock (5.7): every entry or exit of material is a row, in order, with its balance, and the
ledger of an item always adds up to its stock. Readable only where the inventory is."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.domains.inventory import service as inventory_service
from app.domains.inventory.models import InventoryItem, InventoryMovement, Warehouse
from app.domains.inventory.movements import Movement, MovementType, SourceType
from app.domains.organizations.enums import LinkStatus
from app.domains.organizations.models import EcaAssociationLink
from tests import factories

ADJUSTMENT = Movement(MovementType.adjustment)


def _party(db, org_type, name):
    org = factories.make_organization(db, org_type, legal_name=name)
    role = "eca_admin" if org_type == "eca" else "association_admin"
    return SimpleNamespace(org=org, admin=factories.make_user(db, org_type, role_code=role, organization_id=org.id))


@pytest.fixture
def w(db):
    eca, other = _party(db, "eca", "ECA Alfa"), _party(db, "eca", "ECA Beta")
    linked, unlinked = _party(db, "association", "Asociación Xenón"), _party(db, "association", "Asociación Yodo")
    warehouses = db.query(Warehouse).order_by(Warehouse.name).all()
    warehouses[0].organization_id, warehouses[1].organization_id = eca.org.id, other.org.id
    db.add(EcaAssociationLink(eca_id=eca.org.id, association_id=linked.org.id, status=LinkStatus.active))
    db.commit()
    return SimpleNamespace(eca=eca, other=other, linked=linked, unlinked=unlinked, wh=warehouses[0], wh_other=warehouses[1])


def _buy(client_as, admin, warehouse, kg="10", material="plastic", price="400"):
    """A walk-in purchase: weigh and validate. Returns the weighing id."""
    r = client_as(admin).post("/weighings", json={
        "material_code": material, "warehouse_id": str(warehouse.id), "kg": kg, "price_per_kg": price,
        "seller": {"full_name": "Ana Vendedora", "id_type": "CC", "id_number": "52123456"}})
    assert r.status_code == 201, r.text
    weighing_id = r.json()["id"]
    v = client_as(admin).patch(f"/weighings/{weighing_id}/status", json={"status": "validated"})
    assert v.status_code == 200, v.text
    return weighing_id


def _sell(client_as, admin, warehouse, kg, material="plastic"):
    r = client_as(admin).post("/transactions", json={
        "material_code": material, "warehouse_id": str(warehouse.id), "kg": kg, "price_per_kg": "900"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _ledger(db, warehouse, material="plastic"):
    return db.scalars(select(InventoryMovement).where(
        InventoryMovement.warehouse_id == warehouse.id, InventoryMovement.material_code == material,
    ).order_by(InventoryMovement.seq)).all()


def _adds_up(db):
    """Every item: the deltas sum to the stock, and each balance follows from the previous one."""
    for item in db.scalars(select(InventoryItem)).all():
        rows = _ledger(db, item.warehouse, item.material_code)
        assert sum((r.kg_delta for r in rows), Decimal("0")) == item.stock_kg
        running = Decimal("0")
        for r in rows:
            running += r.kg_delta
            assert r.balance_after_kg == running


class TestEveryChangeIsRecorded:
    def test_a_validated_weighing_is_a_purchase_pointing_at_it_and_at_who_validated(self, client_as, db, w):
        weighing_id = _buy(client_as, w.eca.admin, w.wh, "12.50")
        (row,) = _ledger(db, w.wh)
        assert row.movement_type == "purchase" and row.kg_delta == Decimal("12.50")
        assert row.balance_after_kg == Decimal("12.50") and row.price_per_kg == Decimal("400.00")
        assert row.source_type == "weighing" and str(row.source_id) == weighing_id and row.actor_id == w.eca.admin.id

    def test_a_sale_is_a_negative_movement_pointing_at_the_transaction(self, client_as, db, w):
        _buy(client_as, w.eca.admin, w.wh, "30")
        transaction_id = _sell(client_as, w.eca.admin, w.wh, "12")
        sale = _ledger(db, w.wh)[-1]
        assert sale.movement_type == "sale" and sale.kg_delta == Decimal("-12.00")
        assert sale.balance_after_kg == Decimal("18.00") and sale.price_per_kg is None
        assert sale.source_type == "transaction" and str(sale.source_id) == transaction_id
        assert sale.actor_id == w.eca.admin.id

    def test_cancelling_a_sale_gives_the_material_back_and_says_so(self, client_as, db, w):
        _buy(client_as, w.eca.admin, w.wh, "30")
        transaction_id = _sell(client_as, w.eca.admin, w.wh, "12")
        r = client_as(w.eca.admin).patch(f"/transactions/{transaction_id}/status", json={"status": "cancelled"})
        assert r.status_code == 200, r.text
        back = _ledger(db, w.wh)[-1]
        assert back.movement_type == "sale_cancellation" and back.kg_delta == Decimal("12.00")
        assert back.balance_after_kg == Decimal("30.00") and str(back.source_id) == transaction_id
        assert back.actor_id == w.eca.admin.id

    def test_the_balance_chain_follows_every_step_and_adds_up_to_the_stock(self, client_as, db, w):
        _buy(client_as, w.eca.admin, w.wh, "40")
        _buy(client_as, w.eca.admin, w.wh, "10")
        sale = _sell(client_as, w.eca.admin, w.wh, "25")
        client_as(w.eca.admin).patch(f"/transactions/{sale}/status", json={"status": "cancelled"})
        _sell(client_as, w.eca.admin, w.wh, "5")
        assert [r.balance_after_kg for r in _ledger(db, w.wh)] == [
            Decimal(x) for x in ("40", "50", "25", "50", "45")]
        _adds_up(db)

    def test_two_materials_or_warehouses_keep_separate_chains(self, client_as, db, w):
        _buy(client_as, w.eca.admin, w.wh, "10", "plastic")
        _buy(client_as, w.eca.admin, w.wh, "7", "glass")
        _buy(client_as, w.other.admin, w.wh_other, "3", "plastic")
        assert [r.balance_after_kg for r in _ledger(db, w.wh, "plastic")] == [Decimal("10")]
        assert [r.balance_after_kg for r in _ledger(db, w.wh, "glass")] == [Decimal("7")]
        assert [r.balance_after_kg for r in _ledger(db, w.wh_other)] == [Decimal("3")]
        _adds_up(db)

    def test_a_refused_sale_leaves_no_movement(self, client_as, db, w):
        _buy(client_as, w.eca.admin, w.wh, "5")
        r = client_as(w.eca.admin).post("/transactions", json={
            "material_code": "plastic", "warehouse_id": str(w.wh.id), "kg": "50", "price_per_kg": "900"})
        assert r.status_code == 400
        assert len(_ledger(db, w.wh)) == 1
        _adds_up(db)

    def test_a_rejected_weighing_moves_nothing(self, client_as, db, w):
        r = client_as(w.eca.admin).post("/weighings", json={
            "material_code": "plastic", "warehouse_id": str(w.wh.id), "kg": "10", "price_per_kg": "400",
            "seller": {"full_name": "Ana Vendedora", "id_type": "CC", "id_number": "52123456"}})
        client_as(w.eca.admin).patch(f"/weighings/{r.json()['id']}/status",
                                     json={"status": "rejected", "rejection_reason": "No coincide el peso"})
        assert _ledger(db, w.wh) == []

    def test_an_amount_that_is_not_positive_is_a_programming_error(self, db, w):
        for kg in (Decimal("0"), Decimal("-1")):
            with pytest.raises(ValueError):
                inventory_service.add_stock(db, "plastic", w.wh.id, kg, None, movement=ADJUSTMENT)
            with pytest.raises(ValueError):
                inventory_service.subtract_stock(db, "plastic", w.wh.id, kg, movement=ADJUSTMENT)

    def test_a_manual_adjustment_and_a_loss_are_recorded_with_their_type(self, db, w):
        inventory_service.add_stock(db, "plastic", w.wh.id, Decimal("20"), Decimal("500"), movement=ADJUSTMENT)
        inventory_service.subtract_stock(db, "plastic", w.wh.id, Decimal("3"), movement=Movement(MovementType.loss))
        assert [(r.movement_type, r.kg_delta) for r in _ledger(db, w.wh)] == [
            ("adjustment", Decimal("20.00")), ("loss", Decimal("-3.00"))]
        _adds_up(db)

    def test_source_types_are_the_two_that_exist(self):
        assert {s.value for s in SourceType} == {"weighing", "transaction"}


class TestTheLedgerCannotBeRewritten:
    def test_the_database_rejects_updates_and_deletes(self, db, w):
        inventory_service.add_stock(db, "plastic", w.wh.id, Decimal("5"), None, movement=ADJUSTMENT)
        db.commit()  # a rollback below must not take the movement with it
        row_id = _ledger(db, w.wh)[0].id
        with pytest.raises(DBAPIError, match="append-only"):
            db.execute(text("UPDATE inventory_movements SET kg_delta = 99 WHERE id = :i"), {"i": row_id})
        db.rollback()
        with pytest.raises(DBAPIError, match="append-only"):
            db.execute(text("DELETE FROM inventory_movements WHERE id = :i"), {"i": row_id})
        db.rollback()
        assert _ledger(db, w.wh)[0].kg_delta == Decimal("5.00")

    def test_the_database_refuses_a_zero_movement_a_negative_balance_or_an_unknown_type(self, db, w):
        def insert(**overrides):
            values = {"material_code": "plastic", "warehouse_id": w.wh.id, "movement_type": "adjustment",
                      "kg_delta": Decimal("1"), "balance_after_kg": Decimal("1")} | overrides
            db.add(InventoryMovement(**values))
            with pytest.raises(Exception):  # noqa: B017 - any integrity error
                db.flush()
            db.rollback()

        insert(kg_delta=Decimal("0"))
        insert(balance_after_kg=Decimal("-1"))
        insert(movement_type="gift")


class TestReadingTheLedger:
    def _movements(self, client_as, user, **params):
        r = client_as(user).get("/inventory/movements", params={"limit": 100, **params})
        assert r.status_code == 200, r.text
        return r.json()

    def test_it_lists_newest_first_with_the_material_the_warehouse_and_the_balance(self, client_as, w):
        _buy(client_as, w.eca.admin, w.wh, "30")
        _sell(client_as, w.eca.admin, w.wh, "12")
        body = self._movements(client_as, w.eca.admin)
        assert body["total"] == 2
        newest, oldest = body["items"]
        assert (newest["movement_type"], Decimal(newest["kg_delta"]), Decimal(newest["balance_after_kg"])) == (
            "sale", Decimal("-12"), Decimal("18"))
        assert oldest["movement_type"] == "purchase" and oldest["source_type"] == "weighing"
        assert newest["material"]["code"] == "plastic" and newest["warehouse"]["id"] == str(w.wh.id)
        assert newest["actor_id"] == str(w.eca.admin.id)

    def test_the_route_is_not_taken_for_an_inventory_item_id(self, client_as, w):
        assert client_as(w.eca.admin).get("/inventory/movements").status_code == 200

    def test_it_filters_by_material_warehouse_and_type(self, client_as, w):
        _buy(client_as, w.eca.admin, w.wh, "10", "plastic")
        _buy(client_as, w.eca.admin, w.wh, "7", "glass")
        _sell(client_as, w.eca.admin, w.wh, "4", "plastic")
        by = lambda **p: [i["movement_type"] + ":" + i["material"]["code"]  # noqa: E731
                          for i in self._movements(client_as, w.eca.admin, **p)["items"]]
        assert by(material_code="glass") == ["purchase:glass"]
        assert by(movement_type="sale") == ["sale:plastic"]
        assert sorted(by(warehouse_id=str(w.wh.id))) == ["purchase:glass", "purchase:plastic", "sale:plastic"]
        assert by(movement_type="loss") == []

    def test_it_filters_by_date_with_the_end_excluded(self, client_as, db, w):
        _buy(client_as, w.eca.admin, w.wh, "10")
        row = _ledger(db, w.wh)[0]
        before = (row.occurred_at - timedelta(minutes=1)).isoformat()
        after = (row.occurred_at + timedelta(minutes=1)).isoformat()
        assert self._movements(client_as, w.eca.admin, date_from=before, date_to=after)["total"] == 1
        assert self._movements(client_as, w.eca.admin, date_from=after)["total"] == 0
        assert self._movements(client_as, w.eca.admin, date_to=row.occurred_at.isoformat())["total"] == 0
        far = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        assert self._movements(client_as, w.eca.admin, date_to=far)["total"] == 1

    def test_pages_do_not_overlap_even_when_movements_share_a_timestamp(self, client_as, w):
        for _ in range(5):
            _buy(client_as, w.eca.admin, w.wh, "1")
        first = self._movements(client_as, w.eca.admin, limit=2, offset=0)
        second = self._movements(client_as, w.eca.admin, limit=2, offset=2)
        third = self._movements(client_as, w.eca.admin, limit=2, offset=4)
        ids = [i["id"] for page in (first, second, third) for i in page["items"]]
        assert first["total"] == 5 and len(ids) == 5 and len(set(ids)) == 5

    def test_an_invalid_type_or_limit_is_422(self, client_as, w):
        assert client_as(w.eca.admin).get("/inventory/movements", params={"movement_type": "gift"}).status_code == 422
        assert client_as(w.eca.admin).get("/inventory/movements", params={"limit": 101}).status_code == 422


class TestWhoSeesWhat:
    def test_another_eca_sees_none_of_it(self, client_as, w):
        _buy(client_as, w.eca.admin, w.wh, "10")
        assert self._total(client_as, w.other.admin) == 0

    def test_an_association_linked_to_the_eca_reads_it_and_an_unlinked_one_does_not(self, client_as, w):
        _buy(client_as, w.eca.admin, w.wh, "10")
        assert self._total(client_as, w.linked.admin) == 1
        assert self._total(client_as, w.unlinked.admin) == 0

    def test_each_eca_sees_only_its_own_movements(self, client_as, w):
        _buy(client_as, w.eca.admin, w.wh, "10")
        _buy(client_as, w.other.admin, w.wh_other, "3")
        assert self._total(client_as, w.eca.admin) == 1 and self._total(client_as, w.other.admin) == 1

    def test_filtering_by_another_organizations_warehouse_finds_nothing(self, client_as, w):
        _buy(client_as, w.other.admin, w.wh_other, "3")
        r = client_as(w.eca.admin).get("/inventory/movements", params={"warehouse_id": str(w.wh_other.id)})
        assert r.status_code == 200 and r.json()["total"] == 0

    def test_an_eca_account_without_an_organization_sees_nothing(self, client_as, db, w):
        _buy(client_as, w.eca.admin, w.wh, "10")
        orphan = factories.make_user(db, "eca", role_code="eca_admin", organization_id=None)
        assert self._total(client_as, orphan) == 0

    def test_citizens_recyclers_and_anonymous_callers_are_refused(self, client_as, client, db, w):
        for user in (factories.make_user(db, "citizen"), factories.make_user(db, "recycler")):
            assert client_as(user).get("/inventory/movements").status_code == 403
        assert client.get("/inventory/movements").status_code in (401, 403)

    def test_a_platform_account_cannot_use_the_customer_api(self, client_as, db):
        platform = factories.make_user(db, "platform", role_code="platform_admin")
        assert client_as(platform).get("/inventory/movements").status_code == 403

    def _total(self, client_as, user):
        r = client_as(user).get("/inventory/movements")
        assert r.status_code == 200, r.text
        return r.json()["total"]
