"""GET /ecas/nearby: the ECAs actively linked to the actor's association inside a radius, nearest first."""
import math
from types import SimpleNamespace

import pytest

from app.core.spatial import point
from app.domains.organizations.enums import LinkStatus, OrganizationStatus
from app.domains.organizations.models import EcaAssociationLink
from tests import factories

# Around Portal Usme (Bogotá): one block, about a kilometre and a half, and about six kilometres away.
CENTER = (4.4694, -74.1297)
NEAR = (4.4700, -74.1300)
MID = (4.4830, -74.1297)
FAR = (4.5240, -74.1297)
HOURS = [{"day": "monday", "opens_at": "08:00", "closes_at": "12:00"}]


def _haversine(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371008.8 * math.asin(math.sqrt(h))


def _eca(db, association, where, name, materials=("paper", "plastic"), link=LinkStatus.active, **overrides):
    overrides.setdefault("accepted_materials", None if materials is None else list(materials))
    org = factories.make_organization(
        db, "eca", legal_name=name, city="Bogotá", reception_hours=HOURS,
        location=None if where is None else point(*where), **overrides)
    if link is not None:
        db.add(EcaAssociationLink(eca_id=org.id, association_id=association.org.id, status=link))
        db.commit()
    return org


def _nearby(client_as, user, **params):
    params = {"lat": CENTER[0], "lng": CENTER[1], **params}
    return client_as(user).get("/ecas/nearby", params=params)


@pytest.fixture
def association(db):
    org = factories.make_organization(db, "association", legal_name="Asociación Usme")
    return SimpleNamespace(
        org=org,
        admin=factories.make_user(db, "association", role_code="association_admin", organization_id=org.id),
        operator=factories.make_user(db, "association", role_code="association_operator", organization_id=org.id),
        manager=factories.make_user(db, "association", role_code="route_manager", organization_id=org.id))


@pytest.fixture
def ecas(db, association):
    """Three linked ECAs at increasing distance (created farthest first on purpose)."""
    far = _eca(db, association, FAR, "ECA Lejos")
    mid = _eca(db, association, MID, "ECA Media")
    near = _eca(db, association, NEAR, "ECA Cerca")
    return SimpleNamespace(far=far, mid=mid, near=near)


class TestNearestFirst:
    def test_the_nearest_comes_first_with_its_distance(self, client_as, association, ecas):
        body = _nearby(client_as, association.operator, radius=10_000).json()
        assert [i["id"] for i in body["items"]] == [str(ecas.near.id), str(ecas.mid.id), str(ecas.far.id)]
        assert body["total"] == 3

    def test_the_distances_are_meters_on_the_ground(self, client_as, association, ecas):
        items = _nearby(client_as, association.operator, radius=10_000).json()["items"]
        for item, where in zip(items, (NEAR, MID, FAR)):
            assert item["distance_meters"] == pytest.approx(_haversine(CENTER, where), rel=0.01)

    def test_each_eca_carries_what_the_dispatcher_needs(self, client_as, association, ecas):
        item = _nearby(client_as, association.operator).json()["items"][0]
        assert item["legal_name"] == "ECA Cerca" and item["city"] == "Bogotá"
        assert (item["latitude"], item["longitude"]) == ("4.470000", "-74.130000")
        assert item["reception_hours"] == HOURS
        assert item["accepted_materials"] == ["paper", "plastic"]
        assert "tax_id" not in item and "status" not in item


class TestTheRadius:
    def test_only_what_is_inside_the_radius_comes_back(self, client_as, association, ecas):
        body = _nearby(client_as, association.operator, radius=3_000).json()
        assert [i["legal_name"] for i in body["items"]] == ["ECA Cerca", "ECA Media"]

    def test_the_default_radius_is_five_kilometres(self, client_as, association, ecas):
        assert _nearby(client_as, association.operator).json()["total"] == 2

    def test_the_total_counts_everything_inside_not_just_the_page(self, client_as, association, ecas):
        body = _nearby(client_as, association.operator, radius=10_000, limit=1, offset=1).json()
        assert body["total"] == 3 and [i["legal_name"] for i in body["items"]] == ["ECA Media"]

    def test_a_point_with_nothing_around_is_an_empty_list(self, client_as, association, ecas):
        assert _nearby(client_as, association.operator, lat=6.2, lng=-75.5).json() == {"total": 0, "items": []}


class TestTheMaterial:
    def test_only_ecas_that_take_it_are_listed(self, client_as, association, ecas, db):
        _eca(db, association, (4.4701, -74.1301), "ECA Solo vidrio", materials=("glass",))
        body = _nearby(client_as, association.operator, radius=10_000, material="glass").json()
        assert [i["legal_name"] for i in body["items"]] == ["ECA Solo vidrio"]
        assert _nearby(client_as, association.operator, radius=10_000, material="paper").json()["total"] == 3

    def test_an_eca_that_declared_no_materials_is_left_out_when_one_is_asked_for(self, client_as, association, db):
        _eca(db, association, NEAR, "ECA Sin declarar", materials=None)
        assert _nearby(client_as, association.operator, material="paper").json()["total"] == 0
        assert _nearby(client_as, association.operator).json()["total"] == 1


class TestTheScope:
    def test_an_eca_without_an_active_link_is_not_listed(self, client_as, association, db):
        for index, state in enumerate((LinkStatus.requested, LinkStatus.rejected, LinkStatus.removed, None)):
            _eca(db, association, NEAR, f"ECA {index}", link=state)
        assert _nearby(client_as, association.operator).json() == {"total": 0, "items": []}

    def test_an_eca_that_is_not_approved_is_not_listed(self, client_as, association, db):
        _eca(db, association, NEAR, "ECA suspendida", status=OrganizationStatus.suspended)
        assert _nearby(client_as, association.operator).json()["total"] == 0

    def test_an_eca_without_a_location_is_not_listed(self, client_as, association, db):
        _eca(db, association, None, "ECA sin ubicación")
        assert _nearby(client_as, association.operator).json()["total"] == 0

    def test_another_association_sees_none_of_them(self, client_as, association, ecas, db):
        other = factories.make_organization(db, "association", legal_name="Otra Asociación")
        outsider = factories.make_user(db, "association", role_code="association_operator", organization_id=other.id)
        assert _nearby(client_as, outsider, radius=10_000).json() == {"total": 0, "items": []}

    def test_the_whole_association_team_sees_them(self, client_as, association, ecas):
        for user in (association.admin, association.operator, association.manager):
            assert _nearby(client_as, user).json()["total"] == 2

    def test_staff_without_an_organization_see_nothing(self, client_as, ecas, db):
        orphan = factories.make_user(db, "association", role_code="association_operator", organization_id=None)
        assert _nearby(client_as, orphan, radius=10_000).json() == {"total": 0, "items": []}


class TestWhoMayAsk:
    @pytest.mark.parametrize("kind, role", [("eca", "eca_admin"), ("eca", "eca_operator"), ("citizen", None),
                                            ("recycler", None), ("building", None)])
    def test_other_accounts_are_refused(self, client_as, association, ecas, db, kind, role):
        user = factories.make_user(db, kind, role_code=role) if role else factories.make_user(db, kind)
        assert _nearby(client_as, user).status_code == 403

    def test_it_requires_a_session(self):
        from fastapi.testclient import TestClient

        from app.main import app
        assert TestClient(app).get("/ecas/nearby", params={"lat": 4.4, "lng": -74.1}).status_code in (401, 403)


class TestWhatIsRefused:
    @pytest.mark.parametrize("params", [
        {"lat": 91}, {"lat": -91}, {"lng": 181}, {"lng": -181}, {"radius": 0}, {"radius": 50_001},
        {"material": ""}, {"material": "x" * 31}, {"limit": 0}, {"limit": 101}, {"offset": -1}, {"lat": "x"},
    ])
    def test_out_of_range_parameters(self, client_as, association, params):
        assert _nearby(client_as, association.operator, **params).status_code == 422

    def test_the_point_is_required(self, client_as, association):
        assert client_as(association.operator).get("/ecas/nearby").status_code == 422

    def test_the_route_does_not_shadow_the_ecas_own_profile(self, client_as, eca_admin):
        assert client_as(eca_admin).get("/ecas/me/routing-profile").status_code == 200
