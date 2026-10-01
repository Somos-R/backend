"""GET /weighings?sort=&order=&date_from=&date_to= and GET /weighings/export.csv."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core.csv_export import safe_cell
from app.domains.audit.actions import Action
from app.domains.audit.models import AuditLog
from app.domains.inventory.models import Warehouse
from app.domains.weighings import service as weighing_service
from app.domains.weighings.models import Weighing
from tests import factories


@pytest.fixture
def w(db):
    eca = factories.make_organization(db, "eca", legal_name="ECA Alfa")
    other = factories.make_organization(db, "eca", legal_name="ECA Beta")
    wh = db.query(Warehouse).order_by(Warehouse.name).all()
    wh[0].organization_id, wh[1].organization_id = eca.id, other.id
    db.commit()
    return SimpleNamespace(
        wh=wh[0], wh_other=wh[1],
        op=factories.make_user(db, "eca", role_code="eca_operator", organization_id=eca.id),
        other_op=factories.make_user(db, "eca", role_code="eca_operator", organization_id=other.id),
        recycler=factories.make_user(db, "recycler", organization_id=None, full_name="José Gómez", id_number="20202020"),
    )


def _weigh(client_as, user, warehouse, kg, price="400", name="Ana Vendedora"):
    body = {"material_code": "plastic", "warehouse_id": str(warehouse.id), "kg": str(kg), "price_per_kg": price,
            "seller": {"full_name": name, "id_type": "CC", "id_number": "52123456"}}
    r = client_as(user).post("/weighings", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _age(db, weighing_id, days):
    row = db.get(Weighing, weighing_id)
    row.occurred_at = datetime.now(timezone.utc) - timedelta(days=days)
    db.commit()


@pytest.fixture
def three(client_as, db, w):
    ids = [_weigh(client_as, w.op, w.wh, kg) for kg in (30, 10, 20)]  # ages 1, 10, 20 days
    for weighing_id, days in zip(ids, (1, 10, 20)):
        _age(db, weighing_id, days)
    return SimpleNamespace(a=ids[0], b=ids[1], c=ids[2])


def _listed(client_as, user, **params):
    r = client_as(user).get("/weighings", params={"limit": 100, **params})
    assert r.status_code == 200, r.text
    return [i["id"] for i in r.json()["items"]]


class TestSort:
    def test_default_is_newest_first(self, client_as, w, three):
        assert _listed(client_as, w.op) == [three.a, three.b, three.c]

    def test_by_kg_in_both_directions(self, client_as, w, three):
        assert _listed(client_as, w.op, sort="kg", order="asc") == [three.b, three.c, three.a]
        assert _listed(client_as, w.op, sort="kg", order="desc") == [three.a, three.c, three.b]

    def test_by_total_value(self, client_as, w, three):
        cheap = _weigh(client_as, w.op, w.wh, 100, price="1")  # 100 total, the lowest
        assert _listed(client_as, w.op, sort="total_value", order="asc")[0] == cheap

    def test_unknown_column_is_rejected(self, client_as, w):
        r = client_as(w.op).get("/weighings", params={"sort": "seller_name; drop table users"})
        assert r.status_code == 422

    def test_pages_do_not_overlap_when_values_tie(self, client_as, w):
        ids = {_weigh(client_as, w.op, w.wh, 5) for _ in range(5)}
        seen = []
        for offset in (0, 2, 4):
            seen += _listed(client_as, w.op, sort="kg", limit=2, offset=offset)
        assert set(seen) == ids and len(seen) == 5


class TestPeriod:
    def test_from_and_to(self, client_as, w, three):
        now = datetime.now(timezone.utc)
        week_ago = (now - timedelta(days=7)).isoformat()
        assert _listed(client_as, w.op, date_from=week_ago) == [three.a]
        assert _listed(client_as, w.op, date_to=week_ago) == [three.b, three.c]
        window = {"date_from": (now - timedelta(days=15)).isoformat(), "date_to": (now - timedelta(days=5)).isoformat()}
        assert _listed(client_as, w.op, **window) == [three.b]

    def test_total_follows_the_period(self, client_as, w, three):
        r = client_as(w.op).get("/weighings", params={"date_from": (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()})
        assert r.json()["total"] == 1

    def test_bad_date_is_rejected(self, client_as, w):
        assert client_as(w.op).get("/weighings", params={"date_from": "yesterday"}).status_code == 422


class TestExport:
    def test_csv_with_the_filters_and_every_row(self, client_as, w, three):
        r = client_as(w.op).get("/weighings/export.csv", params={"sort": "kg", "order": "asc"})
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        assert "attachment" in r.headers["content-disposition"]
        lines = r.text.lstrip("﻿").strip().splitlines()
        assert lines[0].startswith("date,delivered_by")
        assert len(lines) == 4  # header + every matching row, not one page
        assert [line.split(",")[7] for line in lines[1:]] == ["10.00", "20.00", "30.00"]

    def test_respects_the_period(self, client_as, w, three):
        r = client_as(w.op).get("/weighings/export.csv", params={
            "date_from": (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()})
        assert len(r.text.strip().splitlines()) == 2

    def test_only_what_the_actor_may_read(self, client_as, w, three):
        r = client_as(w.other_op).get("/weighings/export.csv")
        assert r.status_code == 200
        assert len(r.text.strip().splitlines()) == 1  # the header only: nothing of the other ECA

    def test_a_recycler_cannot_export(self, client_as, w):
        assert client_as(w.recycler).get("/weighings/export.csv").status_code == 403

    def test_formula_text_stays_text(self, client_as, w):
        _weigh(client_as, w.op, w.wh, 10, name="=HYPERLINK(\"http://x\")")
        assert "'=HYPERLINK" in client_as(w.op).get("/weighings/export.csv").text

    def test_safe_cell(self):
        assert safe_cell("+57300") == "'+57300"
        assert safe_cell("María") == "María"
        assert safe_cell(None) == ""

    def test_too_many_rows_is_refused_not_truncated(self, client_as, w, three, monkeypatch):
        monkeypatch.setattr(weighing_service, "MAX_EXPORT_ROWS", 2)
        r = client_as(w.op).get("/weighings/export.csv")
        assert r.status_code == 400 and r.json()["code"] == "export_too_large"

    def test_is_audited_without_personal_data(self, client_as, db, w, three):
        client_as(w.op).get("/weighings/export.csv")
        entry = db.query(AuditLog).filter(AuditLog.action == Action.WEIGHINGS_EXPORTED).one()
        assert entry.details == {"rows": 3}
