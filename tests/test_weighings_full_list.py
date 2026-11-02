"""GET /weighings/all: everything the user may read, newest first, with a safety cap (the browser filters and pages)."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update

from app.core.config import settings
from app.domains.weighings.models import Weighing
from tests import factories


def _register(client_as, user, recycler, warehouse, material="plastic", kg="10"):
    r = client_as(user).post("/weighings", json={
        "recycler_id": str(recycler.id), "material_code": material, "warehouse_id": str(warehouse.id),
        "kg": kg, "price_per_kg": "400"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.fixture
def five(client_as, eca_admin, recycler, warehouse, db):
    """Five weighings, one per day, the first registered being the oldest. Returns their ids oldest first."""
    now = datetime.now(timezone.utc)
    ids = []
    for days_ago, material in zip((4, 3, 2, 1, 0), ("plastic", "glass", "paper", "metal", "plastic")):
        weighing_id = _register(client_as, eca_admin, recycler, warehouse, material=material)
        db.execute(update(Weighing).where(Weighing.id == weighing_id).values(occurred_at=now - timedelta(days=days_ago)))
        ids.append(weighing_id)
    db.commit()
    return ids


class TestTheList:
    def test_it_returns_everything_newest_first_with_the_real_total(self, client_as, eca_admin, five):
        r = client_as(eca_admin).get("/weighings/all")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 5 and body["truncated"] is False
        assert [item["id"] for item in body["items"]] == list(reversed(five))

    def test_rows_have_the_same_shape_as_the_paginated_list(self, client_as, eca_admin, five):
        c = client_as(eca_admin)
        everything = {item["id"]: item for item in c.get("/weighings/all").json()["items"]}
        paged = {item["id"]: item for item in c.get("/weighings", params={"limit": 100}).json()["items"]}
        assert everything == paged

    def test_it_is_the_same_rows_in_the_same_order_as_paging_through_the_list(self, client_as, eca_admin, five):
        c = client_as(eca_admin)
        paged = []
        for offset in (0, 2, 4):
            paged += [i["id"] for i in c.get("/weighings", params={"limit": 2, "offset": offset}).json()["items"]]
        assert [i["id"] for i in c.get("/weighings/all").json()["items"]] == paged

    def test_it_takes_no_filters(self, client_as, eca_admin, five):
        """What the browser narrows down is not the server's business: unknown parameters change nothing."""
        body = client_as(eca_admin).get("/weighings/all", params={"status": "paid", "material_code": "glass", "limit": 1}).json()
        assert body["total"] == 5 and len(body["items"]) == 5

    def test_with_nothing_registered_it_is_empty(self, client_as, eca_admin):
        body = client_as(eca_admin).get("/weighings/all").json()
        assert body == {"total": 0, "truncated": False, "items": []}


class TestTheCap:
    def test_more_than_the_cap_returns_the_newest_and_says_so(self, client_as, eca_admin, five, monkeypatch):
        monkeypatch.setattr(settings, "weighings_full_list_max_rows", 3)
        body = client_as(eca_admin).get("/weighings/all").json()
        assert body["total"] == 5 and body["truncated"] is True
        assert [item["id"] for item in body["items"]] == list(reversed(five))[:3]

    def test_exactly_the_cap_is_not_truncated(self, client_as, eca_admin, five, monkeypatch):
        monkeypatch.setattr(settings, "weighings_full_list_max_rows", 5)
        body = client_as(eca_admin).get("/weighings/all").json()
        assert body["total"] == 5 and body["truncated"] is False and len(body["items"]) == 5

    def test_the_default_cap_is_ten_thousand(self):
        assert settings.weighings_full_list_max_rows == 10_000


class TestWhoMayRead:
    def test_staff_that_cannot_read_weighings_are_refused(self, client_as, db):
        manager = factories.make_user(db, "association", role_code="route_manager")
        assert client_as(manager).get("/weighings/all").status_code == 403

    def test_a_citizen_is_refused(self, client_as, citizen):
        assert client_as(citizen).get("/weighings/all").status_code == 403

    def test_it_requires_a_session(self):
        from fastapi.testclient import TestClient

        from app.main import app
        assert TestClient(app).get("/weighings/all").status_code in (401, 403)

    def test_the_route_is_not_taken_for_a_weighing_id(self, client_as, eca_admin):
        """`/all` is declared before `/{weighing_id}`: it must not answer 422 for a malformed id."""
        assert client_as(eca_admin).get("/weighings/all").status_code == 200
