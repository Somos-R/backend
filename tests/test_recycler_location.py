"""PUT /recyclers/{id}/location: the recycler's own app says where they are now, or where their day starts."""
from datetime import datetime, timedelta, timezone

import pytest
from geoalchemy2 import Geometry
from sqlalchemy import cast, func, select

from app.domains.audit.models import AuditLog
from app.domains.users.models import User
from tests import factories


def _put(client_as, user, recycler_id, **body):
    return client_as(user).put(f"/recyclers/{recycler_id}/location", json=body)


def _stored(db, user, column):
    db.expire_all()
    row = db.execute(select(
        func.ST_Y(cast(column, Geometry)), func.ST_X(cast(column, Geometry)), User.location_updated_at
    ).where(User.id == user.id)).one()
    return None if row[0] is None else (round(row[0], 6), round(row[1], 6)), row[2]


NOW = datetime.now(timezone.utc)


class TestTheCurrentPosition:
    def test_it_is_stored_with_the_time_of_the_reading(self, client_as, recycler, db):
        reading = NOW - timedelta(minutes=2)
        r = _put(client_as, recycler, recycler.id, latitude="4.469400", longitude="-74.129700",
                 timestamp=reading.isoformat())
        assert r.status_code == 200, r.text
        assert r.json()["applied"] is True and r.json()["kind"] == "current"
        position, updated_at = _stored(db, recycler, User.current_location)
        assert position == (4.4694, -74.1297) and updated_at == reading

    def test_the_server_time_is_used_when_the_app_sends_none(self, client_as, recycler, db):
        _put(client_as, recycler, recycler.id, latitude="4.5", longitude="-74.1")
        _, updated_at = _stored(db, recycler, User.current_location)
        assert abs((datetime.now(timezone.utc) - updated_at).total_seconds()) < 30

    def test_a_newer_reading_replaces_the_stored_one(self, client_as, recycler, db):
        _put(client_as, recycler, recycler.id, latitude="4.50", longitude="-74.10",
             timestamp=(NOW - timedelta(minutes=10)).isoformat())
        r = _put(client_as, recycler, recycler.id, latitude="4.51", longitude="-74.11",
                 timestamp=(NOW - timedelta(minutes=5)).isoformat())
        assert r.json()["applied"] is True
        assert _stored(db, recycler, User.current_location)[0] == (4.51, -74.11)

    def test_an_older_reading_is_ignored_and_says_so(self, client_as, recycler, db):
        _put(client_as, recycler, recycler.id, latitude="4.51", longitude="-74.11",
             timestamp=(NOW - timedelta(minutes=5)).isoformat())
        r = _put(client_as, recycler, recycler.id, latitude="4.50", longitude="-74.10",
                 timestamp=(NOW - timedelta(minutes=10)).isoformat())
        assert r.status_code == 200 and r.json()["applied"] is False
        assert _stored(db, recycler, User.current_location)[0] == (4.51, -74.11)

    def test_the_same_instant_does_not_replace_it(self, client_as, recycler, db):
        stamp = (NOW - timedelta(minutes=3)).isoformat()
        _put(client_as, recycler, recycler.id, latitude="4.51", longitude="-74.11", timestamp=stamp)
        assert _put(client_as, recycler, recycler.id, latitude="4.60", longitude="-74.20",
                    timestamp=stamp).json()["applied"] is False

    def test_a_time_without_a_zone_counts_as_utc(self, client_as, recycler, db):
        naive = (NOW - timedelta(minutes=1)).replace(tzinfo=None).isoformat()
        assert _put(client_as, recycler, recycler.id, latitude="4.5", longitude="-74.1", timestamp=naive).status_code == 200

    def test_a_reading_from_the_future_is_refused(self, client_as, recycler, db):
        r = _put(client_as, recycler, recycler.id, latitude="4.5", longitude="-74.1",
                 timestamp=(NOW + timedelta(hours=1)).isoformat())
        assert r.status_code == 422 and r.json()["code"] == "timestamp_in_future"
        assert _stored(db, recycler, User.current_location)[0] is None

    def test_a_little_clock_skew_is_tolerated(self, client_as, recycler):
        r = _put(client_as, recycler, recycler.id, latitude="4.5", longitude="-74.1",
                 timestamp=(datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat())
        assert r.status_code == 200


class TestWhereTheDayStarts:
    def test_it_sets_the_start_point_and_leaves_the_current_position_alone(self, client_as, recycler, db):
        _put(client_as, recycler, recycler.id, latitude="4.50", longitude="-74.10")
        r = _put(client_as, recycler, recycler.id, kind="start", latitude="4.40", longitude="-74.20")
        assert r.status_code == 200 and r.json()["kind"] == "start" and r.json()["updated_at"] is None
        assert _stored(db, recycler, User.start_location)[0] == (4.4, -74.2)
        assert _stored(db, recycler, User.current_location)[0] == (4.5, -74.1)


class TestOnlyTheRecyclerThemself:
    def test_another_recycler_is_refused(self, client_as, recycler, db):
        other = factories.make_user(db, "recycler")
        r = _put(client_as, other, recycler.id, latitude="4.5", longitude="-74.1")
        assert r.status_code == 403 and r.json()["code"] == "location_own_only"
        assert _stored(db, recycler, User.current_location)[0] is None

    @pytest.mark.parametrize("kind, role", [
        ("association", "association_admin"), ("eca", "eca_admin"), ("citizen", None)])
    def test_nobody_else_moves_a_recycler(self, client_as, recycler, db, kind, role):
        user = factories.make_user(db, kind, role_code=role) if role else factories.make_user(db, kind)
        assert _put(client_as, user, recycler.id, latitude="4.5", longitude="-74.1").status_code == 403

    def test_a_recycler_cannot_reach_through_another_ones_id(self, client_as, recycler, db):
        other = factories.make_user(db, "recycler")
        assert _put(client_as, recycler, other.id, latitude="4.5", longitude="-74.1").status_code == 403

    def test_it_requires_a_session(self, recycler):
        from fastapi.testclient import TestClient

        from app.main import app
        r = TestClient(app).put(f"/recyclers/{recycler.id}/location", json={"latitude": "4.5", "longitude": "-74.1"})
        assert r.status_code in (401, 403)


class TestWhatIsRefused:
    @pytest.mark.parametrize("body", [
        {"latitude": "91", "longitude": "-74.1"}, {"latitude": "-91", "longitude": "-74.1"},
        {"latitude": "4.5", "longitude": "181"}, {"latitude": "4.5", "longitude": "-181"},
        {"latitude": "4.1234567", "longitude": "-74.1"}, {"latitude": "abc", "longitude": "-74.1"},
        {"longitude": "-74.1"}, {"latitude": "4.5"}, {"latitude": "4.5", "longitude": "-74.1", "kind": "somewhere"},
    ])
    def test_coordinates_out_of_range_or_malformed(self, client_as, recycler, body, db):
        assert _put(client_as, recycler, recycler.id, **body).status_code == 422
        assert _stored(db, recycler, User.current_location)[0] is None


class TestPrivacy:
    def test_a_location_update_leaves_nothing_in_the_audit_trail(self, client_as, recycler, db):
        before = db.query(AuditLog).count()
        _put(client_as, recycler, recycler.id, latitude="4.5", longitude="-74.1")
        _put(client_as, recycler, recycler.id, kind="start", latitude="4.4", longitude="-74.2")
        assert db.query(AuditLog).count() == before
