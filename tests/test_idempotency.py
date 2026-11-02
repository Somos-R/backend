"""Idempotent creation (Sprint 5): a retry with the same `Idempotency-Key` returns the original response and
creates nothing; the same key with another body is refused."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, update

from app.core import idempotency
from app.core.idempotency import IdempotencyKey
from app.domains.collections.models import Collection
from app.domains.weighings.models import Weighing
from app.domains.weighings.schemas import CreateWeighingRequest

WEIGHING = {"material_code": "plastic", "kg": "25.5", "price_per_kg": "400"}
COLLECTION = {"address": "Calle 45 # 12-30, Bogotá", "latitude": "4.636000", "longitude": "-74.065000",
              "materials": ["paper"]}


def _weighing_body(recycler, warehouse, **changes):
    return {**WEIGHING, "recycler_id": str(recycler.id), "warehouse_id": str(warehouse.id), **changes}


def _headers(key):
    return {"Idempotency-Key": str(key)}


def _weighings(db):
    return db.scalar(select(func.count()).select_from(Weighing))


def _collections(db):
    return db.scalar(select(func.count()).select_from(Collection))


@pytest.fixture
def scale(client_as, eca_admin):
    return client_as(eca_admin)


class TestWeighings:
    def test_a_retry_with_the_same_key_returns_the_original_and_creates_nothing(
            self, scale, recycler, warehouse, db):
        key = uuid.uuid4()
        body = _weighing_body(recycler, warehouse)
        first = scale.post("/weighings", json=body, headers=_headers(key))
        retry = scale.post("/weighings", json=body, headers=_headers(key))
        assert first.status_code == retry.status_code == 201, retry.text
        assert retry.json() == first.json()
        assert _weighings(db) == 1

    def test_the_body_is_compared_by_content_not_by_key_order(self, scale, recycler, warehouse, db):
        key = uuid.uuid4()
        first = scale.post("/weighings", json=_weighing_body(recycler, warehouse), headers=_headers(key))
        reordered = dict(reversed(list(_weighing_body(recycler, warehouse).items())))
        retry = scale.post("/weighings", json=reordered, headers=_headers(key))
        assert retry.status_code == 201 and retry.json()["id"] == first.json()["id"]
        assert _weighings(db) == 1

    def test_the_same_key_with_another_body_is_refused(self, scale, recycler, warehouse, db):
        key = uuid.uuid4()
        scale.post("/weighings", json=_weighing_body(recycler, warehouse), headers=_headers(key))
        r = scale.post("/weighings", json=_weighing_body(recycler, warehouse, kg="30"), headers=_headers(key))
        assert r.status_code == 422 and r.json()["code"] == "idempotency_key_reused"
        assert _weighings(db) == 1

    def test_different_keys_are_different_weighings(self, scale, recycler, warehouse, db):
        body = _weighing_body(recycler, warehouse)
        first = scale.post("/weighings", json=body, headers=_headers(uuid.uuid4())).json()
        second = scale.post("/weighings", json=body, headers=_headers(uuid.uuid4())).json()
        assert first["id"] != second["id"] and _weighings(db) == 2

    def test_without_a_key_nothing_changes(self, scale, recycler, warehouse, db):
        body = _weighing_body(recycler, warehouse)
        assert scale.post("/weighings", json=body).status_code == 201
        assert scale.post("/weighings", json=body).status_code == 201
        assert _weighings(db) == 2
        assert db.scalar(select(func.count()).select_from(IdempotencyKey)) == 0

    def test_a_failed_attempt_leaves_the_key_free(self, scale, recycler, warehouse, db):
        key = uuid.uuid4()
        failed = scale.post("/weighings", json=_weighing_body(recycler, warehouse, material_code="unobtainium"),
                            headers=_headers(key))
        assert failed.status_code == 404
        r = scale.post("/weighings", json=_weighing_body(recycler, warehouse), headers=_headers(key))
        assert r.status_code == 201, r.text
        assert _weighings(db) == 1

    def test_a_key_still_being_processed_answers_409(self, scale, recycler, warehouse, eca_admin, db):
        body = _weighing_body(recycler, warehouse)
        key = uuid.uuid4()
        request_hash = idempotency.fingerprint(CreateWeighingRequest.model_validate(body))
        db.add(IdempotencyKey(user_id=eca_admin.id, operation="weighings.create", key=key, request_hash=request_hash))
        db.commit()
        r = scale.post("/weighings", json=body, headers=_headers(key))
        assert r.status_code == 409 and r.json()["code"] == "idempotency_request_in_progress"
        assert _weighings(db) == 0

    def test_an_expired_key_is_a_new_request(self, scale, recycler, warehouse, db):
        key = uuid.uuid4()
        body = _weighing_body(recycler, warehouse)
        first = scale.post("/weighings", json=body, headers=_headers(key)).json()
        db.execute(update(IdempotencyKey).values(
            created_at=datetime.now(timezone.utc) - idempotency.KEY_TTL - timedelta(minutes=1)))
        db.commit()
        again = scale.post("/weighings", json=body, headers=_headers(key))
        assert again.status_code == 201 and again.json()["id"] != first["id"]
        assert _weighings(db) == 2

    def test_the_key_must_be_a_uuid(self, scale, recycler, warehouse, db):
        r = scale.post("/weighings", json=_weighing_body(recycler, warehouse), headers=_headers("retry-1"))
        assert r.status_code == 422 and r.json()["code"] == "validation_error"
        assert _weighings(db) == 0

    def test_the_stored_row_holds_the_response_never_more_than_the_fingerprint_of_the_body(
            self, scale, recycler, warehouse, db):
        key = uuid.uuid4()
        created = scale.post("/weighings", json=_weighing_body(recycler, warehouse), headers=_headers(key)).json()
        row = db.scalars(select(IdempotencyKey)).one()
        assert (row.operation, row.key, row.response_status) == ("weighings.create", key, 201)
        assert row.response_body == created and len(row.request_hash) == 64


class TestCollections:
    def test_a_retry_with_the_same_key_returns_the_original_and_creates_nothing(self, client_as, citizen, db):
        client, key = client_as(citizen), uuid.uuid4()
        first = client.post("/collections", json=COLLECTION, headers=_headers(key))
        retry = client.post("/collections", json=COLLECTION, headers=_headers(key))
        assert first.status_code == retry.status_code == 201
        assert retry.json() == first.json() and _collections(db) == 1

    def test_the_same_key_with_another_body_is_refused(self, client_as, citizen, db):
        client, key = client_as(citizen), uuid.uuid4()
        client.post("/collections", json=COLLECTION, headers=_headers(key))
        r = client.post("/collections", json={**COLLECTION, "materials": ["glass"]}, headers=_headers(key))
        assert r.status_code == 422 and r.json()["code"] == "idempotency_key_reused"
        assert _collections(db) == 1

    def test_one_key_may_serve_a_collection_and_a_weighing(
            self, client_as, citizen, scale, recycler, warehouse, db):
        key = uuid.uuid4()
        assert client_as(citizen).post("/collections", json=COLLECTION, headers=_headers(key)).status_code == 201
        assert scale.post("/weighings", json=_weighing_body(recycler, warehouse),
                          headers=_headers(key)).status_code == 201
        assert _collections(db) == 1 and _weighings(db) == 1


def test_browsers_may_send_the_header():
    from app.main import CORS_HEADERS

    assert idempotency.HEADER in CORS_HEADERS
