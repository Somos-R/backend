"""Pickup requests (B-1.1, REC-01, REC-02, REC-03): a citizen or a building asks, only they see their own, and they
may cancel while nobody has been assigned; the association it is assigned to sees it in its inbox and may reject it
with a reason of the catalog; a request rejected for a correctable reason may be corrected and sent again."""
import uuid
from datetime import timedelta

import pytest
from geoalchemy2 import Geometry
from sqlalchemy import cast, func, select

from app.domains.collections import service as collections_service
from app.domains.collections.models import (
    Collection,
    CollectionRejection,
    CollectionStatus,
    RejectionReason,
)
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


def _set_status(db, collection_id, status: CollectionStatus) -> None:
    collection = db.get(Collection, uuid.UUID(collection_id))
    collection.status = status
    # A rejected request always carries its reason (ck_collections_rejected_has_reason).
    collection.rejection_reason_code = "duplicate_request" if status == CollectionStatus.rejected else None
    db.commit()


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
        _set_status(db, created["id"], current)
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


@pytest.fixture
def association_operator(db):
    org = factories.make_organization(db, "association")
    return factories.make_user(db, "association", role_code="association_operator", organization_id=org.id)


def _assigned_to(client, db, association) -> dict:
    """A citizen's request, assigned to `association` (nothing assigns one through the API yet)."""
    created = _create(client).json()
    db.get(Collection, uuid.UUID(created["id"])).association_id = association.organization_id
    db.commit()
    return created


def _reject(client, collection_id, reason_code="incomplete_address"):
    return client.post(f"/collections/{collection_id}/reject", json={"reason_code": reason_code})


class TestRejectionReasons:
    def test_the_catalog_lists_correctable_reasons_first(self, client_as, citizen):
        r = client_as(citizen).get("/collections/rejection-reasons")
        assert r.status_code == 200, r.text
        assert r.json() == [
            {"code": "incomplete_address", "label": "Dirección incompleta o no ubicable", "is_correctable": True},
            {"code": "insufficient_description", "label": "Foto o descripción insuficiente", "is_correctable": True},
            {"code": "invalid_contact", "label": "Datos de contacto inválidos", "is_correctable": True},
            {"code": "duplicate_request", "label": "Solicitud duplicada", "is_correctable": False},
            {"code": "non_recoverable_material", "label": "Material no aprovechable", "is_correctable": False},
            {"code": "outside_coverage", "label": "Fuera de la zona de cobertura", "is_correctable": False},
        ]

    def test_a_retired_reason_is_not_listed(self, client_as, association_operator, db):
        db.get(RejectionReason, "invalid_contact").is_active = False
        db.commit()
        codes = [r["code"] for r in client_as(association_operator).get("/collections/rejection-reasons").json()]
        assert "invalid_contact" not in codes and len(codes) == 5

    def test_a_token_is_required(self, client):
        assert client.get("/collections/rejection-reasons").status_code == 401


class TestReject:
    def test_the_association_rejects_with_a_reason_and_the_requester_sees_it(
        self, client_as, citizen, association_operator, db,
    ):
        created = _assigned_to(client_as(citizen), db, association_operator)
        r = _reject(client_as(association_operator), created["id"])
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "rejected" and body["rejected_at"] is not None
        assert body["rejection_reason"] == {
            "code": "incomplete_address", "label": "Dirección incompleta o no ubicable", "is_correctable": True}
        assert db.get(Collection, uuid.UUID(created["id"])).rejected_by == association_operator.id

        seen = client_as(citizen).get(f"/collections/{created['id']}").json()
        assert seen["status"] == "rejected" and seen["rejection_reason"]["is_correctable"] is True
        listed = client_as(citizen).get("/collections", params={"status": "rejected"}).json()
        assert listed["items"][0]["rejection_reason"]["code"] == "incomplete_address"

    def test_a_final_reason_says_it_cannot_be_corrected(self, client_as, citizen, association_admin, db):
        created = _assigned_to(client_as(citizen), db, association_admin)
        r = _reject(client_as(association_admin), created["id"], "outside_coverage")
        assert r.status_code == 200 and r.json()["rejection_reason"]["is_correctable"] is False

    def test_a_pending_request_shows_no_rejection(self, client_as, citizen):
        body = _create(client_as(citizen)).json()
        assert body["rejection_reason"] is None and body["rejected_at"] is None and body["association_id"] is None

    @pytest.mark.parametrize("reason_code", ["dirección mala", "kryptonite"])
    def test_only_a_code_of_the_catalog_is_accepted(self, client_as, citizen, association_operator, db, reason_code):
        created = _assigned_to(client_as(citizen), db, association_operator)
        r = _reject(client_as(association_operator), created["id"], reason_code)
        assert r.status_code == 422 and r.json()["code"] == "rejection_reason_not_found"
        assert db.get(Collection, uuid.UUID(created["id"])).status == CollectionStatus.pending_assignment

    def test_a_retired_reason_is_refused(self, client_as, citizen, association_operator, db):
        db.get(RejectionReason, "invalid_contact").is_active = False
        db.commit()
        created = _assigned_to(client_as(citizen), db, association_operator)
        r = _reject(client_as(association_operator), created["id"], "invalid_contact")
        assert r.status_code == 422 and r.json()["code"] == "rejection_reason_not_found"

    @pytest.mark.parametrize("body", [{}, {"reason_code": ""}, {"reason_code": "x" * 41}])
    def test_invalid_bodies_are_refused(self, client_as, citizen, association_operator, db, body):
        created = _assigned_to(client_as(citizen), db, association_operator)
        r = client_as(association_operator).post(f"/collections/{created['id']}/reject", json=body)
        assert r.status_code == 422 and r.json()["code"] == "validation_error"

    @pytest.mark.parametrize("current", [
        CollectionStatus.assigned, CollectionStatus.rejected, CollectionStatus.cancelled])
    def test_only_pending_assignment_can_be_rejected(self, client_as, citizen, association_operator, db, current):
        created = _assigned_to(client_as(citizen), db, association_operator)
        _set_status(db, created["id"], current)
        r = _reject(client_as(association_operator), created["id"])
        assert r.status_code == 409 and r.json()["code"] == "collection_not_rejectable"
        assert db.get(Collection, uuid.UUID(created["id"])).status == current

    def test_a_cancelled_request_cannot_be_rejected_after_the_fact(self, client_as, citizen, association_operator, db):
        c = client_as(citizen)
        created = _assigned_to(c, db, association_operator)
        c.patch(f"/collections/{created['id']}/cancel")
        assert _reject(client_as(association_operator), created["id"]).status_code == 409

    def test_a_request_assigned_to_no_association_is_out_of_reach(self, client_as, citizen, association_operator, db):
        created = _create(client_as(citizen)).json()
        db.get(Collection, uuid.UUID(created["id"])).association_id = None
        db.commit()
        r = _reject(client_as(association_operator), created["id"])
        assert r.status_code == 404 and r.json()["code"] == "collection_not_found"
        assert _reject(client_as(association_operator), uuid.uuid4()).status_code == 404

    def test_staff_without_an_organization_reaches_nothing(self, client_as, citizen, association_operator, db):
        created = _assigned_to(client_as(citizen), db, association_operator)
        stray = factories.make_user(db, "association", role_code="association_operator", organization_id=None)
        assert _reject(client_as(stray), created["id"]).status_code == 404

    @pytest.mark.parametrize("kind", ["citizen", "recycler", "eca_admin", "eca_operator", "route_manager"])
    def test_only_association_admins_and_operators_may_reject(self, client_as, citizen, db, kind):
        created = _create(client_as(citizen)).json()
        actor = factories.make_actor(db, kind)
        assert _reject(client_as(actor), created["id"]).status_code == 403

    def test_the_requester_cannot_reject_their_own(self, client_as, citizen, association_operator, db):
        created = _assigned_to(client_as(citizen), db, association_operator)
        assert _reject(client_as(citizen), created["id"]).status_code == 403
        assert db.get(Collection, uuid.UUID(created["id"])).status == CollectionStatus.pending_assignment

    def test_a_rejected_request_must_carry_its_reason(self, client_as, citizen, db):
        from sqlalchemy.exc import IntegrityError

        created = _create(client_as(citizen)).json()
        db.get(Collection, uuid.UUID(created["id"])).status = CollectionStatus.rejected
        with pytest.raises(IntegrityError, match="ck_collections_rejected_has_reason"):
            db.flush()
        db.rollback()

    def test_listing_rejected_requests_costs_the_same_queries_for_few_and_many(
        self, client_as, citizen, association_operator, db, count_queries,
    ):
        c, operator = client_as(citizen), client_as(association_operator)
        association_id = association_operator.organization_id  # read before expunge_all() detaches it
        reasons = ["incomplete_address", "invalid_contact", "duplicate_request"]

        def reject_more(n):
            for i in range(n):
                created = _create(c).json()
                db.get(Collection, uuid.UUID(created["id"])).association_id = association_id
                db.commit()
                assert _reject(operator, created["id"], reasons[i % 3]).status_code == 200

        def measure():
            db.expunge_all()
            with count_queries() as statements:
                assert c.get("/collections").status_code == 200
            return len(statements)

        reject_more(2)
        few = measure()
        reject_more(9)
        assert measure() == few


def _resubmit(client, collection_id, **changes):
    return client.put(f"/collections/{collection_id}/resubmit", json={**BODY, **changes})


def _history(db, collection_id) -> list[CollectionRejection]:
    return list(db.scalars(
        select(CollectionRejection)
        .where(CollectionRejection.collection_id == uuid.UUID(collection_id))
        .order_by(CollectionRejection.rejected_at, CollectionRejection.id)))


class TestResubmit:
    def _rejected(self, client_as, citizen, operator, db, reason_code="incomplete_address") -> dict:
        created = _assigned_to(client_as(citizen), db, operator)
        assert _reject(client_as(operator), created["id"], reason_code).status_code == 200
        return created

    def test_a_correctable_rejection_is_corrected_and_goes_back_to_the_queue(
        self, client_as, citizen, association_operator, db,
    ):
        created = self._rejected(client_as, citizen, association_operator, db)
        r = _resubmit(client_as(citizen), created["id"], address="Calle 45 # 12-30 Apto 301, Bogotá",
                      materials=["glass"], notes=None)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "pending_assignment"
        assert body["address"] == "Calle 45 # 12-30 Apto 301, Bogotá"
        assert body["materials"] == ["glass"] and body["notes"] is None
        assert body["rejection_reason"] is None and body["rejected_at"] is None
        assert body["created_at"] == created["created_at"]
        assert body["association_id"] == str(association_operator.organization_id)
        assert db.get(Collection, uuid.UUID(created["id"])).rejected_by is None

    def test_the_corrected_point_moves_the_postgis_location(self, client_as, citizen, association_operator, db):
        created = self._rejected(client_as, citizen, association_operator, db)
        _resubmit(client_as(citizen), created["id"], latitude="4.500000", longitude="-74.100000")
        as_geometry = cast(Collection.location, Geometry)
        lon, lat = db.execute(
            select(func.ST_X(as_geometry), func.ST_Y(as_geometry))
            .where(Collection.id == uuid.UUID(created["id"]))).one()
        assert (round(lon, 6), round(lat, 6)) == (-74.1, 4.5)

    def test_it_keeps_its_place_in_the_association_inbox(self, client_as, citizen, association_operator, db):
        c = client_as(citizen)
        oldest = self._rejected(client_as, citizen, association_operator, db)
        newer = _assigned_to(c, db, association_operator)
        _created_ago(db, oldest["id"], 48)
        _created_ago(db, newer["id"], 24)
        assert _resubmit(c, oldest["id"]).status_code == 200

        inbox = _inbox(client_as(association_operator), association_operator.organization_id,
                       status="pending_assignment").json()
        assert [item["id"] for item in inbox["items"]] == [oldest["id"], newer["id"]]

    def test_every_rejection_and_resubmission_stays_in_the_history(
        self, client_as, citizen, association_operator, db,
    ):
        created = self._rejected(client_as, citizen, association_operator, db, "incomplete_address")
        _resubmit(client_as(citizen), created["id"])
        assert _reject(client_as(association_operator), created["id"], "invalid_contact").status_code == 200

        history = _history(db, created["id"])
        assert [row.reason_code for row in history] == ["incomplete_address", "invalid_contact"]
        assert all(row.rejected_by == association_operator.id for row in history)
        assert history[0].resubmitted_at is not None and history[1].resubmitted_at is None

    def test_a_final_rejection_cannot_be_resubmitted(self, client_as, citizen, association_operator, db):
        created = self._rejected(client_as, citizen, association_operator, db, "outside_coverage")
        r = _resubmit(client_as(citizen), created["id"])
        assert r.status_code == 409 and r.json()["code"] == "collection_rejection_final"
        assert db.get(Collection, uuid.UUID(created["id"])).status == CollectionStatus.rejected
        assert _history(db, created["id"])[0].resubmitted_at is None

    @pytest.mark.parametrize("current", [
        CollectionStatus.pending_assignment, CollectionStatus.assigned, CollectionStatus.cancelled])
    def test_only_a_rejected_request_can_be_resubmitted(self, client_as, citizen, db, current):
        created = _create(client_as(citizen)).json()
        _set_status(db, created["id"], current)
        r = _resubmit(client_as(citizen), created["id"])
        assert r.status_code == 409 and r.json()["code"] == "collection_not_resubmittable"

    def test_the_resubmissions_are_limited(self, client_as, citizen, association_operator, db, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "collection_max_resubmissions", 2)
        created = self._rejected(client_as, citizen, association_operator, db)
        for _ in range(2):
            assert _resubmit(client_as(citizen), created["id"]).status_code == 200
            assert _reject(client_as(association_operator), created["id"]).status_code == 200
        r = _resubmit(client_as(citizen), created["id"])
        assert r.status_code == 409 and r.json()["code"] == "collection_resubmission_limit_reached"
        assert db.get(Collection, uuid.UUID(created["id"])).status == CollectionStatus.rejected

    def test_a_rejection_from_before_the_history_is_recorded_when_resubmitted(self, client_as, citizen, db):
        created = _create(client_as(citizen)).json()
        collection = db.get(Collection, uuid.UUID(created["id"]))
        collection.status, collection.rejection_reason_code = CollectionStatus.rejected, "invalid_contact"
        db.commit()
        assert _resubmit(client_as(citizen), created["id"]).status_code == 200
        [row] = _history(db, created["id"])
        assert row.reason_code == "invalid_contact" and row.resubmitted_at is not None

    def test_a_request_without_an_association_is_assigned_like_a_new_one(self, client_as, citizen, db):
        created = _create(client_as(citizen)).json()  # no association exists yet: nobody is assigned
        association = factories.make_organization(db, "association")
        _set_status(db, created["id"], CollectionStatus.rejected)
        db.get(Collection, uuid.UUID(created["id"])).rejection_reason_code = "incomplete_address"
        db.commit()
        r = _resubmit(client_as(citizen), created["id"])
        assert r.status_code == 200 and r.json()["association_id"] == str(association.id)

    def test_the_corrected_request_follows_the_rules_of_a_new_one(
        self, client_as, citizen, association_operator, db,
    ):
        created = self._rejected(client_as, citizen, association_operator, db)
        c = client_as(citizen)
        yesterday = collections_service._local_today() - timedelta(days=1)
        r = _resubmit(c, created["id"], scheduled_date=str(yesterday))
        assert r.status_code == 422 and r.json()["code"] == "scheduled_date_in_past"
        assert _resubmit(c, created["id"], materials=["kryptonite"]).status_code == 404
        assert _resubmit(c, created["id"], address="x").json()["code"] == "validation_error"
        assert db.get(Collection, uuid.UUID(created["id"])).status == CollectionStatus.rejected
        assert _history(db, created["id"])[0].resubmitted_at is None

    def test_someone_elses_request_does_not_exist(self, client_as, citizen, association_operator, db):
        created = self._rejected(client_as, citizen, association_operator, db)
        other = factories.make_user(db, "citizen")
        r = _resubmit(client_as(other), created["id"])
        assert r.status_code == 404 and r.json()["code"] == "collection_not_found"
        assert _resubmit(client_as(citizen), uuid.uuid4()).status_code == 404

    @pytest.mark.parametrize("kind", ["recycler", "association_operator", "eca_admin"])
    def test_only_requesters_may_resubmit(self, client_as, citizen, db, kind):
        created = _create(client_as(citizen)).json()
        r = _resubmit(client_as(factories.make_actor(db, kind)), created["id"])
        assert r.status_code == 403 and r.json()["code"] == "collections_requesters_only"

    def test_a_token_is_required(self, client):
        assert client.put(f"/collections/{uuid.uuid4()}/resubmit", json=BODY).status_code == 401


def _inbox(client, association_id, **params):
    return client.get(f"/associations/{association_id}/collections", params=params)


def _created_ago(db, collection_id, hours: int) -> None:
    """Requests made in one test share the transaction's now(): spread them in time by hand."""
    collection = db.get(Collection, uuid.UUID(collection_id))
    collection.created_at = collection.created_at - timedelta(hours=hours)
    db.commit()


class TestAssociationInbox:
    def test_the_association_sees_its_requests_oldest_first(self, client_as, citizen, association_operator, db):
        c = client_as(citizen)
        newest = _assigned_to(c, db, association_operator)
        oldest = _assigned_to(c, db, association_operator)
        middle = _assigned_to(c, db, association_operator)
        _created_ago(db, oldest["id"], 48)
        _created_ago(db, middle["id"], 24)

        r = _inbox(client_as(association_operator), association_operator.organization_id)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 3
        assert [item["id"] for item in body["items"]] == [oldest["id"], middle["id"], newest["id"]]
        assert body["items"][0]["materials"] == BODY["materials"]
        assert body["items"][0]["association_id"] == str(association_operator.organization_id)

    def test_requests_of_nobody_or_of_another_association_are_left_out(
        self, client_as, citizen, association_operator, db,
    ):
        c = client_as(citizen)
        unassigned = _create(c).json()
        db.get(Collection, uuid.UUID(unassigned["id"])).association_id = None
        db.commit()
        other = factories.make_user(db, "association", role_code="association_operator",
                                    organization_id=factories.make_organization(db, "association").id)
        _assigned_to(c, db, other)
        mine = _assigned_to(c, db, association_operator)

        body = _inbox(client_as(association_operator), association_operator.organization_id).json()
        assert body["total"] == 1 and [item["id"] for item in body["items"]] == [mine["id"]]

    def test_the_inbox_filters_by_status_and_paginates(self, client_as, citizen, association_operator, db):
        c, operator = client_as(citizen), client_as(association_operator)
        association_id = association_operator.organization_id
        pending = [_assigned_to(c, db, association_operator) for _ in range(3)]
        rejected = _assigned_to(c, db, association_operator)
        assert _reject(operator, rejected["id"]).status_code == 200

        only_rejected = _inbox(operator, association_id, status="rejected").json()
        assert only_rejected["total"] == 1
        assert only_rejected["items"][0]["rejection_reason"]["code"] == "incomplete_address"

        page = _inbox(operator, association_id, status="pending_assignment", limit=2, offset=2).json()
        assert page["total"] == 3 and len(page["items"]) == 1
        assert page["items"][0]["id"] in {p["id"] for p in pending}

    @pytest.mark.parametrize("params", [{"status": "lost"}, {"limit": 0}, {"limit": 101}, {"offset": -1}])
    def test_invalid_filters_are_refused(self, client_as, association_operator, params):
        r = _inbox(client_as(association_operator), association_operator.organization_id, **params)
        assert r.status_code == 422

    def test_an_admin_of_the_association_sees_it_too(self, client_as, citizen, association_operator, db):
        admin = factories.make_user(db, "association", role_code="association_admin",
                                    organization_id=association_operator.organization_id)
        _assigned_to(client_as(citizen), db, association_operator)
        assert _inbox(client_as(admin), admin.organization_id).json()["total"] == 1

    def test_another_associations_id_does_not_exist(self, client_as, association_operator, db):
        other = factories.make_organization(db, "association")
        for association_id in (other.id, uuid.uuid4()):
            r = _inbox(client_as(association_operator), association_id)
            assert r.status_code == 404 and r.json()["code"] == "organization_not_found"

    def test_staff_without_an_organization_reaches_nothing(self, client_as, db):
        loose = factories.make_user(db, "association", role_code="association_operator")
        r = _inbox(client_as(loose), uuid.uuid4())
        assert r.status_code == 404 and r.json()["code"] == "organization_not_found"

    @pytest.mark.parametrize("kind", ["citizen", "eca_admin", "recycler"])
    def test_only_association_admins_and_operators_may_read_it(self, client_as, db, kind):
        user = (factories.make_user(db, "eca", role_code="eca_admin") if kind == "eca_admin"
                else factories.make_user(db, kind))
        assert _inbox(client_as(user), uuid.uuid4()).status_code == 403

    def test_a_token_is_required(self, client):
        assert _inbox(client, uuid.uuid4()).status_code == 401

    def test_the_inbox_costs_the_same_queries_for_few_and_many(
        self, client_as, citizen, association_operator, db, count_queries,
    ):
        c, operator = client_as(citizen), client_as(association_operator)
        association_id = association_operator.organization_id  # read before expunge_all() detaches it

        def assign_and_reject(n):
            for _ in range(n):
                created = _create(c).json()
                db.get(Collection, uuid.UUID(created["id"])).association_id = association_id
                db.commit()
                assert _reject(operator, created["id"]).status_code == 200

        def measure():
            db.expunge_all()
            with count_queries() as statements:
                assert _inbox(operator, association_id).status_code == 200
            return len(statements)

        assign_and_reject(2)
        few = measure()
        assign_and_reject(9)
        assert measure() == few


class TestAssignmentToTheAssociation:
    """A request is assigned when it is created: the pilot has a single association, so it is the only approved one."""

    def test_the_only_approved_association_gets_the_request(self, client_as, citizen, association_operator):
        body = _create(client_as(citizen)).json()
        assert body["association_id"] == str(association_operator.organization_id)
        assert body["status"] == "pending_assignment"

    def test_it_shows_up_in_that_associations_inbox_and_nearby_map(self, client_as, citizen, association_operator):
        created = _create(client_as(citizen), latitude="4.4700", longitude="-74.1300").json()
        operator = client_as(association_operator)
        inbox = _inbox(operator, association_operator.organization_id).json()
        assert [item["id"] for item in inbox["items"]] == [created["id"]]
        nearby = operator.get("/collections/nearby", params={"lat": 4.47, "lng": -74.13}).json()
        assert [item["id"] for item in nearby["items"]] == [created["id"]]

    def test_with_no_association_the_request_stays_unassigned(self, client_as, citizen):
        assert _create(client_as(citizen)).json()["association_id"] is None

    def test_with_two_associations_and_no_area_to_tell_them_apart_nobody_is_assigned(
        self, client_as, citizen, association_operator, db,
    ):
        factories.make_organization(db, "association", legal_name="Otra Asociación")
        assert _create(client_as(citizen)).json()["association_id"] is None

    def test_an_association_that_is_not_approved_is_not_assigned(self, client_as, citizen, db):
        from app.domains.organizations.enums import OrganizationStatus
        factories.make_organization(db, "association", status=OrganizationStatus.draft)
        assert _create(client_as(citizen)).json()["association_id"] is None

    def test_an_eca_is_never_assigned(self, client_as, citizen, eca_admin):
        assert _create(client_as(citizen)).json()["association_id"] is None

    def test_a_request_from_the_offline_batch_is_assigned_the_same_way(self, client_as, citizen, association_operator):
        import uuid as uuid_module
        from datetime import datetime, timezone
        operation = {"type": "collection", "idempotency_key": str(uuid_module.uuid4()),
                     "client_created_at": datetime(2026, 11, 3, tzinfo=timezone.utc).isoformat(),
                     "payload": {"address": "Calle 1", "latitude": "4.47", "longitude": "-74.13",
                                 "materials": ["paper"]}}
        record = client_as(citizen).post("/sync/batch", json={"operations": [operation]}).json()["results"][0]["record"]
        assert record["association_id"] == str(association_operator.organization_id)
