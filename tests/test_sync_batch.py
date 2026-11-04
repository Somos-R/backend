"""POST /sync/batch: what a device saved offline, processed one by one with the same rules and idempotency as the
endpoints that create each kind of record."""
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.domains.collections.models import Collection
from app.domains.sync import service as sync_service
from app.domains.weighings import service as weighings_service
from app.domains.weighings.models import Weighing
from tests import factories

BASE = datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc)


def _weighing(recycler, warehouse, minutes=0, key=None, **payload):
    return {
        "type": "weighing", "idempotency_key": str(key or uuid.uuid4()),
        "client_created_at": (BASE + timedelta(minutes=minutes)).isoformat(),
        "payload": {"recycler_id": str(recycler.id), "material_code": "plastic", "warehouse_id": str(warehouse.id),
                    "kg": "10", "price_per_kg": "400", **payload}}


def _collection(minutes=0, key=None, **payload):
    return {
        "type": "collection", "idempotency_key": str(key or uuid.uuid4()),
        "client_created_at": (BASE + timedelta(minutes=minutes)).isoformat(),
        "payload": {"address": "Calle 45 # 12-30", "latitude": "4.636", "longitude": "-74.065",
                    "materials": ["paper"], **payload}}


def _send(client_as, user, *operations):
    r = client_as(user).post("/sync/batch", json={"operations": list(operations)})
    assert r.status_code == 200, r.text
    return r.json()


class TestAMixedBatch:
    def test_every_operation_gets_its_own_result_in_the_order_of_the_request(
        self, client_as, eca_admin, recycler, warehouse, db,
    ):
        good = _weighing(recycler, warehouse, minutes=2)
        bad_material = _weighing(recycler, warehouse, minutes=1, material_code="unobtainium")
        bad_shape = _weighing(recycler, warehouse, minutes=0, kg="-5")
        body = _send(client_as, eca_admin, good, bad_material, bad_shape)

        assert [r["index"] for r in body["results"]] == [0, 1, 2]
        assert [r["status"] for r in body["results"]] == ["created", "error", "error"]
        assert body["summary"] == {"created": 1, "duplicate": 0, "error": 2}
        assert body["results"][0]["record"]["status"] == "pending_validation"
        assert body["results"][1]["error"]["code"] == "material_not_found"
        assert body["results"][2]["error"]["code"] == "validation_error"
        assert isinstance(body["results"][2]["error"]["detail"], list)  # the errors of the payload
        assert db.query(Weighing).count() == 1

    def test_a_failure_does_not_undo_what_came_before_or_after_it(self, client_as, eca_admin, recycler, warehouse, db):
        body = _send(client_as, eca_admin,
                     _weighing(recycler, warehouse, minutes=0),
                     _weighing(recycler, warehouse, minutes=1, material_code="unobtainium"),
                     _weighing(recycler, warehouse, minutes=2))
        assert [r["status"] for r in body["results"]] == ["created", "error", "created"]
        assert db.query(Weighing).count() == 2

    def test_weighings_and_pickup_requests_travel_in_their_own_batches(
        self, client_as, eca_admin, citizen, recycler, warehouse, db,
    ):
        eca = _send(client_as, eca_admin, _weighing(recycler, warehouse))
        person = _send(client_as, citizen, _collection())
        assert eca["results"][0]["status"] == person["results"][0]["status"] == "created"
        assert db.query(Weighing).count() == 1 and db.query(Collection).count() == 1
        assert person["results"][0]["record"]["status"] == "pending_assignment"


class TestSendingTheBatchAgain:
    def test_what_was_created_comes_back_as_a_duplicate_and_nothing_is_created_twice(
        self, client_as, eca_admin, recycler, warehouse, db,
    ):
        operations = [_weighing(recycler, warehouse, minutes=i) for i in range(3)]
        first = _send(client_as, eca_admin, *operations)
        again = _send(client_as, eca_admin, *operations)

        assert [r["status"] for r in again["results"]] == ["duplicate"] * 3
        assert again["summary"] == {"created": 0, "duplicate": 3, "error": 0}
        assert [r["record"]["id"] for r in again["results"]] == [r["record"]["id"] for r in first["results"]]
        assert db.query(Weighing).count() == 3

    def test_only_what_failed_is_tried_again(self, client_as, eca_admin, recycler, warehouse, db):
        failing = _weighing(recycler, warehouse, minutes=1, material_code="unobtainium")
        ok = _weighing(recycler, warehouse, minutes=0)
        _send(client_as, eca_admin, ok, failing)
        failing["payload"]["material_code"] = "plastic"  # the device fixed it and resends the same batch
        again = _send(client_as, eca_admin, ok, failing)
        assert [r["status"] for r in again["results"]] == ["duplicate", "created"]
        assert db.query(Weighing).count() == 2

    def test_the_same_key_with_another_content_is_refused(self, client_as, eca_admin, recycler, warehouse, db):
        key = uuid.uuid4()
        body = _send(client_as, eca_admin,
                     _weighing(recycler, warehouse, minutes=0, key=key),
                     _weighing(recycler, warehouse, minutes=1, key=key, kg="11"))
        assert [r["status"] for r in body["results"]] == ["created", "error"]
        assert body["results"][1]["error"]["code"] == "idempotency_key_reused"
        assert db.query(Weighing).count() == 1

    def test_a_key_used_through_the_endpoint_is_known_to_the_batch(self, client_as, eca_admin, recycler, warehouse, db):
        operation = _weighing(recycler, warehouse)
        direct = client_as(eca_admin).post(
            "/weighings", json=operation["payload"], headers={"Idempotency-Key": operation["idempotency_key"]})
        assert direct.status_code == 201
        body = _send(client_as, eca_admin, operation)
        assert body["results"][0]["status"] == "duplicate" and body["results"][0]["record"]["id"] == direct.json()["id"]
        assert db.query(Weighing).count() == 1


class TestTheOrderOfTheBatch:
    def test_the_oldest_operation_is_processed_first(self, client_as, eca_admin, recycler, warehouse, db):
        newer = _weighing(recycler, warehouse, minutes=30, kg="2")
        older = _weighing(recycler, warehouse, minutes=5, kg="1")
        body = _send(client_as, eca_admin, newer, older)  # sent newest first
        assert [r["index"] for r in body["results"]] == [0, 1]  # answered in the order of the request
        first_created = db.query(Weighing).order_by(Weighing.created_at, Weighing.occurred_at).first()
        assert first_created.kg == 1  # the older one was recorded first

    def test_the_time_of_the_device_is_returned_and_the_record_gets_the_servers(self, client_as, eca_admin, recycler, warehouse):
        operation = _weighing(recycler, warehouse, minutes=-600)
        body = _send(client_as, eca_admin, operation)
        assert body["results"][0]["client_created_at"].startswith("2026-11-02")
        assert body["results"][0]["record"]["occurred_at"][:10] != "2026-11-02"  # the server's own date


class TestPermissions:
    def test_each_operation_needs_the_permission_of_its_own_endpoint(
        self, client_as, eca_admin, citizen, recycler, warehouse, db,
    ):
        from_the_eca = _send(client_as, eca_admin, _collection(), _weighing(recycler, warehouse))
        assert [r["status"] for r in from_the_eca["results"]] == ["error", "created"]
        assert from_the_eca["results"][0]["error"]["code"] == "collections_requesters_only"

        from_a_citizen = _send(client_as, citizen, _weighing(recycler, warehouse), _collection(minutes=1))
        assert [r["status"] for r in from_a_citizen["results"]] == ["error", "created"]
        assert from_a_citizen["results"][0]["error"]["code"] == "forbidden"

    def test_staff_who_cannot_weigh_get_an_error_per_weighing(self, client_as, eca_admin, recycler, warehouse, db):
        keeper = factories.make_user(db, "eca", role_code="eca_warehouse", organization_id=eca_admin.organization_id)
        body = _send(client_as, keeper, _weighing(recycler, warehouse))
        assert body["results"][0]["error"]["code"] == "forbidden" and db.query(Weighing).count() == 0

    def test_a_session_is_required(self):
        from fastapi.testclient import TestClient

        from app.main import app
        assert TestClient(app).post("/sync/batch", json={"operations": []}).status_code in (401, 403)


class TestTheShapeOfTheBatch:
    def test_it_cannot_be_empty(self, client_as, eca_admin):
        assert client_as(eca_admin).post("/sync/batch", json={"operations": []}).status_code == 422

    def test_it_has_a_size_limit(self, client_as, eca_admin, recycler, warehouse, monkeypatch):
        monkeypatch.setattr(settings, "sync_batch_max_items", 2)
        operations = [_weighing(recycler, warehouse, minutes=i) for i in range(3)]
        r = client_as(eca_admin).post("/sync/batch", json={"operations": operations})
        assert r.status_code == 422 and r.json()["code"] == "validation_error"
        assert client_as(eca_admin).post("/sync/batch", json={"operations": operations[:2]}).status_code == 200

    def test_the_default_limit_is_two_hundred(self):
        assert settings.sync_batch_max_items == 200

    @pytest.mark.parametrize("change", [
        {"type": "route_stop"}, {"idempotency_key": "not-a-uuid"}, {"client_created_at": "yesterday"}, {"payload": "x"},
    ])
    def test_an_operation_with_a_wrong_envelope_rejects_the_whole_request(
        self, client_as, eca_admin, recycler, warehouse, change,
    ):
        operation = {**_weighing(recycler, warehouse), **change}
        assert client_as(eca_admin).post("/sync/batch", json={"operations": [operation]}).status_code == 422


class TestAnUnexpectedFailure:
    def test_it_is_reported_for_that_operation_only_and_logged(
        self, client_as, eca_admin, recycler, warehouse, db, monkeypatch, caplog,
    ):
        real = weighings_service.create_weighing
        calls = []

        def flaky(db_, actor, body):
            calls.append(body.kg)
            if len(calls) == 2:
                raise RuntimeError("boom")
            return real(db_, actor, body)

        monkeypatch.setitem(sync_service._HANDLERS, "weighing", replace(sync_service._HANDLERS["weighing"], create=flaky))
        with caplog.at_level("ERROR"):
            body = _send(client_as, eca_admin, *[_weighing(recycler, warehouse, minutes=i) for i in range(3)])
        assert [r["status"] for r in body["results"]] == ["created", "error", "created"]
        assert body["results"][1]["error"] == {"code": "internal_error", "detail": "No se pudo procesar esta operación"}
        assert "boom" not in str(body)  # nothing internal leaks to the client
        assert any("sync batch" in r.message for r in caplog.records)
        assert db.query(Weighing).count() == 2
