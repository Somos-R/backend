"""GET /transactions?sort=&order=&date_from=&date_to= and GET /transactions/export.csv."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.domains.audit.actions import Action
from app.domains.audit.models import AuditLog
from app.domains.transactions import service as tx_service
from app.domains.transactions.models import Transaction
from tests import factories


@pytest.fixture
def t(db, client_as, eca_admin, warehouse):
    """Three sales of 30, 10 and 20 kg, from 1, 10 and 20 days ago."""
    factories.stock(db, warehouse, kg="1000")
    ids = []
    for kg, days in (("30", 1), ("10", 10), ("20", 20)):
        r = client_as(eca_admin).post("/transactions", json={
            "material_code": "plastic", "warehouse_id": str(warehouse.id), "kg": kg, "price_per_kg": "900",
            "buyer_name": "Industrias Verdes", "buyer_nit": "901234567-8"})
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
        db.get(Transaction, ids[-1]).occurred_at = datetime.now(timezone.utc) - timedelta(days=days)
    db.commit()
    return SimpleNamespace(a=ids[0], b=ids[1], c=ids[2])


def _listed(client_as, user, **params):
    r = client_as(user).get("/transactions", params={"limit": 100, **params})
    assert r.status_code == 200, r.text
    return [i["id"] for i in r.json()["items"]]


def _days_ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


class TestSort:
    def test_default_is_newest_first(self, client_as, eca_admin, t):
        assert _listed(client_as, eca_admin) == [t.a, t.b, t.c]

    def test_by_kg_in_both_directions(self, client_as, eca_admin, t):
        assert _listed(client_as, eca_admin, sort="kg", order="asc") == [t.b, t.c, t.a]
        assert _listed(client_as, eca_admin, sort="kg", order="desc") == [t.a, t.c, t.b]

    def test_by_total_value(self, client_as, eca_admin, t):
        assert _listed(client_as, eca_admin, sort="total_value", order="desc")[0] == t.a

    def test_unknown_column_is_rejected(self, client_as, eca_admin):
        assert client_as(eca_admin).get("/transactions", params={"sort": "buyer_email"}).status_code == 422

    def test_pages_do_not_overlap_when_values_tie(self, client_as, eca_admin, t):
        seen = []
        for offset in (0, 1, 2):
            seen += _listed(client_as, eca_admin, sort="status", limit=1, offset=offset)
        assert sorted(seen) == sorted([t.a, t.b, t.c])


class TestPeriod:
    def test_from_and_to(self, client_as, eca_admin, t):
        assert _listed(client_as, eca_admin, date_from=_days_ago(7)) == [t.a]
        assert _listed(client_as, eca_admin, date_to=_days_ago(7)) == [t.b, t.c]
        assert _listed(client_as, eca_admin, date_from=_days_ago(15), date_to=_days_ago(5)) == [t.b]

    def test_total_follows_the_period(self, client_as, eca_admin, t):
        r = client_as(eca_admin).get("/transactions", params={"date_from": _days_ago(7)})
        assert r.json()["total"] == 1

    def test_bad_date_is_rejected(self, client_as, eca_admin):
        assert client_as(eca_admin).get("/transactions", params={"date_to": "soon"}).status_code == 422


class TestExport:
    def test_csv_with_every_row_in_the_requested_order(self, client_as, eca_admin, t):
        r = client_as(eca_admin).get("/transactions/export.csv", params={"sort": "kg", "order": "asc"})
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        assert "attachment" in r.headers["content-disposition"]
        lines = r.text.lstrip("﻿").strip().splitlines()
        assert lines[0].startswith("date,type,counterparty")
        assert [line.split(",")[6] for line in lines[1:]] == ["10.00", "20.00", "30.00"]
        assert "Industrias Verdes" in lines[1]

    def test_respects_the_period_and_type(self, client_as, eca_admin, t):
        r = client_as(eca_admin).get("/transactions/export.csv", params={"date_from": _days_ago(7)})
        assert len(r.text.strip().splitlines()) == 2
        r = client_as(eca_admin).get("/transactions/export.csv", params={"type": "purchase"})
        assert len(r.text.strip().splitlines()) == 1  # header only: every row here is a sale

    def test_only_what_the_actor_may_read(self, db, client_as, t):
        other = factories.make_organization(db, "eca", legal_name="ECA Beta")
        stranger = factories.make_user(db, "eca", role_code="eca_admin", organization_id=other.id)
        r = client_as(stranger).get("/transactions/export.csv")
        assert r.status_code == 200
        assert len(r.text.strip().splitlines()) == 1

    def test_roles_that_cannot_read_transactions_cannot_export(self, client_as, recycler):
        assert client_as(recycler).get("/transactions/export.csv").status_code == 403

    def test_formula_text_stays_text(self, db, client_as, eca_admin, warehouse):
        factories.stock(db, warehouse, kg="100")
        client_as(eca_admin).post("/transactions", json={
            "material_code": "plastic", "warehouse_id": str(warehouse.id), "kg": "5", "price_per_kg": "900",
            "buyer_name": "=HYPERLINK(\"http://x\")", "buyer_nit": "901234567-8"})
        assert "'=HYPERLINK" in client_as(eca_admin).get("/transactions/export.csv").text

    def test_too_many_rows_is_refused_not_truncated(self, client_as, eca_admin, t, monkeypatch):
        monkeypatch.setattr(tx_service, "MAX_EXPORT_ROWS", 2)
        r = client_as(eca_admin).get("/transactions/export.csv")
        assert r.status_code == 400 and r.json()["code"] == "export_too_large"

    def test_is_audited_without_personal_data(self, db, client_as, eca_admin, t):
        client_as(eca_admin).get("/transactions/export.csv")
        entry = db.query(AuditLog).filter(AuditLog.action == Action.TRANSACTIONS_EXPORTED).one()
        assert entry.details == {"rows": 3}
