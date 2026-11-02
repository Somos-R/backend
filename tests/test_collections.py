"""Pickup requests (B-1.1): a citizen or a building asks, only they see their own, and they may cancel while
nobody has been assigned."""
import uuid
from datetime import timedelta

import pytest
from geoalchemy2 import Geometry
from sqlalchemy import cast, func, select

from app.domains.collections import service as collections_service
from app.domains.collections.models import Collection, CollectionStatus
from tests import factories

BODY = {
    "address": "Calle 45 # 12-30, Bogotá",
    "latitude": "4.636000",
    "longitude": "-74.065000",
    "materials": ["paper", "plastic"],
    "notes": "Portería",
}


def _create(client, **changes):
    return client.post("/collections", json={**BODY, **changes})


class TestCreate:
    def test_a_citizen_asks_and_the_request_starts_pending_assignment(self, client_as, citizen, db):
        r = _create(client_as(citizen))
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "pending_assignment"
        assert body["requester_id"] == str(citizen.id)
        assert body["materials"] == ["paper", "plastic"]
        assert body["scheduled_date"] is None and body["cancelled_at"] is None

    def test_a_building_may_ask_too(self, client_as, db):
        building = factories.make_user(db, "building")
        assert _create(client_as(building)).status_code == 201

    def test_the_location_is_stored_as_a_postgis_point(self, client_as, citizen, db):
        created = _create(client_as(citizen)).json()
        as_geometry = cast(Collection.location, Geometry)
        lon, lat = db.execute(
            select(func.ST_X(as_geometry), func.ST_Y(as_geometry))
            .where(Collection.id == uuid.UUID(created["id"]))).one()
        assert (round(lon, 6), round(lat, 6)) == (-74.065, 4.636)
        assert db.scalar(select(func.ST_SRID(Collection.location)).limit(1)) == 4326

    def test_the_requester_is_the_signed_in_account_never_the_body(self, client_as, citizen, db):
        other = factories.make_user(db, "citizen")
        r = _create(client_as(citizen), requester_id=str(other.id), citizen_id=str(other.id))
        assert r.status_code == 201 and r.json()["requester_id"] == str(citizen.id)

    def test_repeated_materials_are_kept_once(self, client_as, citizen):
        r = _create(client_as(citizen), materials=["paper", "plastic", "paper"])
        assert r.json()["materials"] == ["paper", "plastic"]

    def test_a_scheduled_date_from_today_on_is_accepted(self, client_as, citizen):
        today = collections_service._local_today()
        r = _create(client_as(citizen), scheduled_date=str(today))
        assert r.status_code == 201 and r.json()["scheduled_date"] == str(today)

    def test_a_scheduled_date_in_the_past_is_refused(self, client_as, citizen, db):
        yesterday = collections_service._local_today() - timedelta(days=1)
        r = _create(client_as(citizen), scheduled_date=str(yesterday))
        assert r.status_code == 422 and r.json()["code"] == "scheduled_date_in_past"
        assert db.scalar(select(func.count()).select_from(Collection)) == 0

    def test_an_unknown_material_is_refused(self, client_as, citizen):
        r = _create(client_as(citizen), materials=["kryptonite"])
        assert r.status_code == 404 and r.json()["code"] == "material_not_found"

    def test_a_retired_material_is_refused(self, client_as, citizen, db):
        from app.domains.inventory.models import Material

        db.get(Material, "glass").is_active = False
        db.commit()
        r = _create(client_as(citizen), materials=["glass"])
        assert r.status_code == 400 and r.json()["code"] == "material_inactive"

    @pytest.mark.parametrize("changes", [
        {"materials": []},
        {"materials": [f"m{i}" for i in range(11)]},
        {"materials": [""]},
        {"materials": ["x" * 31]},
        {"latitude": "91"},
        {"longitude": "-181"},
        {"latitude": "4.1234567"},
        {"address": "abc"},
        {"notes": "x" * 501},
    ])
    def test_invalid_bodies_are_refused(self, client_as, citizen, changes):
        r = _create(client_as(citizen), **changes)
        assert r.status_code == 422 and r.json()["code"] == "validation_error"

    @pytest.mark.parametrize("kind", ["recycler", "eca_admin", "association_admin"])
    def test_only_citizens_and_buildings_may_ask(self, client_as, db, kind):
        actor = factories.make_user(db, "recycler") if kind == "recycler" else factories.make_actor(db, kind)
        r = _create(client_as(actor))
        assert r.status_code == 403 and r.json()["code"] == "collections_requesters_only"

    def test_a_token_is_required(self, client):
        assert client.post("/collections", json=BODY).status_code == 401


class TestRead:
    def test_a_requester_lists_only_their_own_newest_first(self, client_as, citizen, db):
        c = client_as(citizen)
        first = _create(c).json()
        second = _create(c).json()
        # One test is one transaction, where now() never moves: give the second a later timestamp.
        newer = db.get(Collection, uuid.UUID(second["id"]))
        newer.created_at = newer.created_at + timedelta(minutes=1)
        db.commit()
        _create(client_as(factories.make_user(db, "citizen")))

        body = c.get("/collections").json()
        assert body["total"] == 2
        assert [i["id"] for i in body["items"]] == [second["id"], first["id"]]

    def test_the_list_filters_by_status_and_paginates(self, client_as, citizen):
        c = client_as(citizen)
        kept = [_create(c).json() for _ in range(3)]
        c.patch(f"/collections/{kept[0]['id']}/cancel")

        pending = c.get("/collections", params={"status": "pending_assignment"}).json()
        assert pending["total"] == 2
        cancelled = c.get("/collections", params={"status": "cancelled"}).json()
        assert [i["id"] for i in cancelled["items"]] == [kept[0]["id"]]
        page = c.get("/collections", params={"limit": 1, "offset": 1}).json()
        assert page["total"] == 3 and len(page["items"]) == 1

    def test_an_unknown_status_is_refused(self, client_as, citizen):
        assert client_as(citizen).get("/collections", params={"status": "pending"}).status_code == 422

    def test_a_requester_reads_the_detail_of_their_own(self, client_as, citizen):
        c = client_as(citizen)
        created = _create(c).json()
        r = c.get(f"/collections/{created['id']}")
        assert r.status_code == 200 and r.json() == created

    def test_someone_elses_request_does_not_exist(self, client_as, citizen, db):
        created = _create(client_as(citizen)).json()
        other = client_as(factories.make_user(db, "citizen"))
        r = other.get(f"/collections/{created['id']}")
        assert r.status_code == 404 and r.json()["code"] == "collection_not_found"
        assert other.get(f"/collections/{uuid.uuid4()}").status_code == 404

    def test_staff_cannot_list_requests(self, client_as, eca_admin):
        r = client_as(eca_admin).get("/collections")
        assert r.status_code == 403 and r.json()["code"] == "collections_requesters_only"

    def test_the_list_costs_the_same_queries_for_few_and_many(self, client_as, citizen, db, count_queries):
        c = client_as(citizen)

        def measure():
            db.expunge_all()
            with count_queries() as statements:
                assert c.get("/collections").status_code == 200
            return len(statements)

        for _ in range(2):
            _create(c)
        few = measure()
        for _ in range(20):
            _create(c)
        assert measure() == few


class TestCancel:
    def test_a_pending_request_is_cancelled_with_its_reason(self, client_as, citizen):
        c = client_as(citizen)
        created = _create(c).json()
        r = c.patch(f"/collections/{created['id']}/cancel", json={"reason": "Ya lo llevé yo"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "cancelled"
        assert body["cancellation_reason"] == "Ya lo llevé yo"
        assert body["cancelled_at"] is not None

    def test_the_reason_is_optional(self, client_as, citizen):
        c = client_as(citizen)
        created = _create(c).json()
        r = c.patch(f"/collections/{created['id']}/cancel")
        assert r.status_code == 200 and r.json()["cancellation_reason"] is None

    @pytest.mark.parametrize("current", [
        CollectionStatus.assigned, CollectionStatus.rejected, CollectionStatus.cancelled])
    def test_only_pending_assignment_can_be_cancelled(self, client_as, citizen, db, current):
        c = client_as(citizen)
        created = _create(c).json()
        db.get(Collection, uuid.UUID(created["id"])).status = current
        db.commit()
        r = c.patch(f"/collections/{created['id']}/cancel")
        assert r.status_code == 409 and r.json()["code"] == "collection_not_cancellable"
        assert db.get(Collection, uuid.UUID(created["id"])).status == current

    def test_a_reason_longer_than_200_characters_is_refused(self, client_as, citizen):
        c = client_as(citizen)
        created = _create(c).json()
        assert c.patch(f"/collections/{created['id']}/cancel", json={"reason": "x" * 201}).status_code == 422

    def test_someone_else_cannot_cancel_it(self, client_as, citizen, db):
        created = _create(client_as(citizen)).json()
        r = client_as(factories.make_user(db, "citizen")).patch(f"/collections/{created['id']}/cancel")
        assert r.status_code == 404 and r.json()["code"] == "collection_not_found"
        assert db.get(Collection, uuid.UUID(created["id"])).status == CollectionStatus.pending_assignment
