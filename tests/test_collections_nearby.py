"""GET /collections/nearby: the requests of the actor's association inside a radius, nearest first."""
import math
from types import SimpleNamespace

import pytest

from app.domains.collections.models import Collection, CollectionStatus
from tests import factories

# Around Portal Usme (Bogotá): one block, about a kilometre and a half, and about six kilometres away.
CENTER = (4.4694, -74.1297)
NEAR = (4.4700, -74.1300)
MID = (4.4830, -74.1297)
FAR = (4.5240, -74.1297)


def _haversine(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371008.8 * math.asin(math.sqrt(h))


def _request(client_as, citizen, where, address="Calle 1"):
    r = client_as(citizen).post("/collections", json={
        "address": address, "latitude": str(where[0]), "longitude": str(where[1]), "materials": ["paper"]})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _nearby(client_as, user, **params):
    params = {"lat": CENTER[0], "lng": CENTER[1], **params}
    return client_as(user).get("/collections/nearby", params=params)


@pytest.fixture
def association(db):
    org = factories.make_organization(db, "association", legal_name="Asociación Usme")
    return SimpleNamespace(
        org=org,
        admin=factories.make_user(db, "association", role_code="association_admin", organization_id=org.id),
        operator=factories.make_user(db, "association", role_code="association_operator", organization_id=org.id),
        manager=factories.make_user(db, "association", role_code="route_manager", organization_id=org.id))


@pytest.fixture
def requests(client_as, citizen, association, db):
    """Three pending requests of the association, at increasing distance (created farthest first on purpose)."""
    ids = SimpleNamespace(far=_request(client_as, citizen, FAR, "Lejos"), mid=_request(client_as, citizen, MID, "Medio"),
                          near=_request(client_as, citizen, NEAR, "Cerca"))
    for collection in db.query(Collection).all():
        collection.association_id = association.org.id
    db.commit()
    return ids


class TestNearestFirst:
    def test_the_nearest_comes_first_with_its_distance(self, client_as, association, requests):
        body = _nearby(client_as, association.operator, radius=10_000).json()
        assert [i["id"] for i in body["items"]] == [requests.near, requests.mid, requests.far]
        distances = [i["distance_meters"] for i in body["items"]]
        assert distances == sorted(distances)

    def test_the_distances_are_meters_on_the_ground(self, client_as, association, requests):
        for item, where in zip(_nearby(client_as, association.operator, radius=10_000).json()["items"], (NEAR, MID, FAR)):
            expected = _haversine(CENTER, where)
            assert abs(item["distance_meters"] - expected) / expected < 0.01  # within 1 % of haversine

    def test_each_request_has_the_shape_of_the_association_inbox(self, client_as, association, requests):
        item = _nearby(client_as, association.operator).json()["items"][0]
        assert {"id", "status", "address", "latitude", "longitude", "materials", "association_id",
                "distance_meters"} <= set(item)
        assert item["association_id"] == str(association.org.id)


class TestTheRadius:
    def test_only_what_is_inside_the_radius_comes_back(self, client_as, association, requests):
        body = _nearby(client_as, association.operator, radius=500).json()
        assert [i["id"] for i in body["items"]] == [requests.near] and body["total"] == 1
        wide = _nearby(client_as, association.operator, radius=3000).json()
        assert [i["id"] for i in wide["items"]] == [requests.near, requests.mid]

    def test_the_default_radius_is_two_kilometres(self, client_as, association, requests):
        assert [i["id"] for i in _nearby(client_as, association.operator).json()["items"]] == [requests.near, requests.mid]

    def test_the_total_counts_everything_inside_not_just_the_page(self, client_as, association, requests):
        body = _nearby(client_as, association.operator, radius=10_000, limit=1).json()
        assert body["total"] == 3 and [i["id"] for i in body["items"]] == [requests.near]
        second = _nearby(client_as, association.operator, radius=10_000, limit=1, offset=1).json()
        assert [i["id"] for i in second["items"]] == [requests.mid]

    def test_a_point_with_nothing_around_is_an_empty_list(self, client_as, association, requests):
        body = _nearby(client_as, association.operator, lat=4.0, lng=-74.0).json()
        assert body == {"total": 0, "items": []}


class TestTheStatus:
    def test_it_asks_for_the_pending_ones_by_default(self, client_as, association, requests, db):
        db.get(Collection, requests.near).status = CollectionStatus.cancelled
        db.commit()
        assert [i["id"] for i in _nearby(client_as, association.operator, radius=10_000).json()["items"]] == [
            requests.mid, requests.far]

    def test_another_status_can_be_asked_for(self, client_as, association, requests, db):
        db.get(Collection, requests.near).status = CollectionStatus.cancelled
        db.commit()
        body = _nearby(client_as, association.operator, radius=10_000, status="cancelled").json()
        assert [i["id"] for i in body["items"]] == [requests.near]


class TestTheScope:
    def test_another_association_sees_none_of_them(self, client_as, association, requests, db):
        other = factories.make_organization(db, "association", legal_name="Otra Asociación")
        outsider = factories.make_user(db, "association", role_code="association_operator", organization_id=other.id)
        assert _nearby(client_as, outsider, radius=10_000).json() == {"total": 0, "items": []}

    def test_requests_nobody_has_been_assigned_are_not_listed(self, client_as, association, requests, db):
        db.get(Collection, requests.near).association_id = None
        db.commit()
        assert requests.near not in [i["id"] for i in _nearby(client_as, association.operator, radius=10_000).json()["items"]]

    def test_the_whole_association_team_sees_them(self, client_as, association, requests):
        for user in (association.admin, association.operator, association.manager):
            assert _nearby(client_as, user).json()["total"] == 2

    def test_staff_without_an_organization_see_nothing(self, client_as, requests, db):
        orphan = factories.make_user(db, "association", role_code="association_operator", organization_id=None)
        assert _nearby(client_as, orphan, radius=10_000).json() == {"total": 0, "items": []}


class TestWhoMayAsk:
    @pytest.mark.parametrize("kind, role", [("eca", "eca_admin"), ("eca", "eca_operator"), ("citizen", None),
                                            ("recycler", None), ("building", None)])
    def test_other_accounts_are_refused(self, client_as, association, requests, db, kind, role):
        user = factories.make_user(db, kind, role_code=role) if role else factories.make_user(db, kind)
        assert _nearby(client_as, user).status_code == 403

    def test_it_requires_a_session(self):
        from fastapi.testclient import TestClient

        from app.main import app
        assert TestClient(app).get("/collections/nearby", params={"lat": 4.4, "lng": -74.1}).status_code in (401, 403)


class TestWhatIsRefused:
    @pytest.mark.parametrize("params", [
        {"lat": 91}, {"lat": -91}, {"lng": 181}, {"lng": -181}, {"radius": 0}, {"radius": -5}, {"radius": 50_001},
        {"status": "lost"}, {"limit": 0}, {"limit": 101}, {"offset": -1}, {"lat": "x"},
    ])
    def test_out_of_range_parameters(self, client_as, association, params):
        assert _nearby(client_as, association.operator, **params).status_code == 422

    def test_the_point_is_required(self, client_as, association):
        assert client_as(association.operator).get("/collections/nearby").status_code == 422
        assert client_as(association.operator).get("/collections/nearby", params={"lat": 4.4}).status_code == 422

    def test_the_route_is_not_taken_for_a_request_id(self, client_as, association, requests):
        assert _nearby(client_as, association.operator).status_code == 200  # it would be a 422 as `{collection_id}`
