"""RUT-01, ECA side: GET/PUT /ecas/me/routing-profile, the location, reception hours and accepted materials that
route planning needs from an ECA."""
import pytest

from app.domains.organizations.enums import OrganizationStatus
from app.domains.organizations.models import Organization
from tests import factories

URL = "/ecas/me/routing-profile"

PROFILE = {
    "latitude": "4.598100",
    "longitude": "-74.075800",
    "reception_hours": [
        {"day": "saturday", "opens_at": "08:00", "closes_at": "12:00"},
        {"day": "monday", "opens_at": "14:00", "closes_at": "17:00"},
        {"day": "monday", "opens_at": "08:00", "closes_at": "12:00"},
    ],
    "accepted_materials": ["paper", "plastic", "paper"],
}


def _put(client_as, user, **changes):
    return client_as(user).put(URL, json={**PROFILE, **changes})


class TestReading:
    def test_a_new_eca_has_nothing_declared_yet(self, client_as, eca_admin):
        r = client_as(eca_admin).get(URL)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["organization_id"] == str(eca_admin.organization_id)
        assert [body[k] for k in ("latitude", "longitude", "reception_hours", "accepted_materials")] == [None] * 4

    @pytest.mark.parametrize("role", ["eca_admin", "eca_operator", "eca_warehouse"])
    def test_every_eca_staff_role_reads_its_own(self, client_as, db, eca_admin, role):
        assert _put(client_as, eca_admin).status_code == 200
        staff = factories.make_user(db, "eca", role_code=role)
        r = client_as(staff).get(URL)
        assert r.status_code == 200 and r.json()["accepted_materials"] == ["paper", "plastic"]

    @pytest.mark.parametrize("kind", ["association_admin", "route_manager", "citizen", "recycler"])
    def test_nobody_outside_an_eca_reads_it(self, client_as, db, kind):
        assert client_as(factories.make_actor(db, kind)).get(URL).status_code == 403

    def test_requires_authentication(self, client):
        assert client.get(URL).status_code in (401, 403)


class TestUpdating:
    def test_the_admin_declares_the_whole_profile(self, client_as, db, eca_admin):
        r = _put(client_as, eca_admin)
        assert r.status_code == 200, r.text
        body = r.json()
        assert (body["latitude"], body["longitude"]) == ("4.598100", "-74.075800")
        # Sorted by day and opening time, repeated materials once.
        assert body["reception_hours"] == [
            {"day": "monday", "opens_at": "08:00", "closes_at": "12:00"},
            {"day": "monday", "opens_at": "14:00", "closes_at": "17:00"},
            {"day": "saturday", "opens_at": "08:00", "closes_at": "12:00"},
        ]
        assert body["accepted_materials"] == ["paper", "plastic"]
        assert client_as(eca_admin).get(URL).json() == body

    def test_the_location_is_stored_as_the_ecas_point(self, client_as, db, eca_admin):
        from sqlalchemy import select

        from app.core.spatial import distance_meters, point

        _put(client_as, eca_admin)
        meters = db.scalar(select(distance_meters(Organization.location, point(4.5981, -74.0758)))
                           .where(Organization.id == eca_admin.organization_id))
        assert meters is not None and meters < 0.01

    def test_put_replaces_what_was_there(self, client_as, eca_admin):
        _put(client_as, eca_admin)
        r = _put(client_as, eca_admin, accepted_materials=["glass"], latitude="4.6", longitude="-74.1",
                 reception_hours=[{"day": "friday", "opens_at": "07:30", "closes_at": "15:45"}])
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["accepted_materials"] == ["glass"]
        assert body["reception_hours"] == [{"day": "friday", "opens_at": "07:30", "closes_at": "15:45"}]
        assert (body["latitude"], body["longitude"]) == ("4.600000", "-74.100000")

    def test_windows_of_one_day_may_touch(self, client_as, eca_admin):
        r = _put(client_as, eca_admin, reception_hours=[
            {"day": "monday", "opens_at": "08:00", "closes_at": "12:00"},
            {"day": "monday", "opens_at": "12:00", "closes_at": "13:00"}])
        assert r.status_code == 200, r.text

    @pytest.mark.parametrize("role", ["eca_operator", "eca_warehouse"])
    def test_only_the_admin_changes_it(self, client_as, db, role):
        staff = factories.make_user(db, "eca", role_code=role)
        assert _put(client_as, staff).status_code == 403

    def test_an_association_admin_cannot_change_it(self, client_as, association_admin):
        assert _put(client_as, association_admin).status_code == 403

    def test_an_eca_that_is_not_approved_cannot_declare_it(self, client_as, db):
        org = factories.make_organization(db, "eca", status=OrganizationStatus.suspended)
        admin = factories.make_user(db, "eca", role_code="eca_admin", organization_id=org.id)
        r = _put(client_as, admin)
        assert r.status_code == 403 and r.json()["code"] == "organization_not_active"
        assert client_as(admin).get(URL).json()["code"] == "organization_not_active"

    def test_an_account_without_organization_fails_closed(self, client_as, db):
        admin = factories.make_user(db, "eca", role_code="eca_admin", organization_id=None)
        r = _put(client_as, admin)
        assert r.status_code == 403 and r.json()["code"] == "no_organization"


class TestValidation:
    @pytest.mark.parametrize("changes", [
        {"latitude": "91"},
        {"longitude": "-181"},
        {"latitude": "4.1234567"},
        {"reception_hours": []},
        {"accepted_materials": []},
        {"accepted_materials": [""]},
        {"accepted_materials": ["x" * 31]},
        {"reception_hours": [{"day": "funday", "opens_at": "08:00", "closes_at": "12:00"}]},
        {"reception_hours": [{"day": "monday", "opens_at": "12:00", "closes_at": "08:00"}]},
        {"reception_hours": [{"day": "monday", "opens_at": "08:00", "closes_at": "08:00"}]},
        {"reception_hours": [{"day": "monday", "opens_at": "08:00:30", "closes_at": "12:00"}]},
        {"reception_hours": [{"day": "monday", "opens_at": "25:00", "closes_at": "26:00"}]},
        {"reception_hours": [
            {"day": "monday", "opens_at": "08:00", "closes_at": "12:00"},
            {"day": "monday", "opens_at": "11:00", "closes_at": "13:00"}]},
        {"reception_hours": [{"day": "monday", "opens_at": "08:00", "closes_at": "09:00"}] * 22},
    ])
    def test_a_malformed_profile_is_a_422_and_changes_nothing(self, client_as, eca_admin, changes):
        r = _put(client_as, eca_admin, **changes)
        assert r.status_code == 422, r.text
        assert client_as(eca_admin).get(URL).json()["accepted_materials"] is None

    def test_every_field_is_required(self, client_as, eca_admin):
        for field in PROFILE:
            body = {k: v for k, v in PROFILE.items() if k != field}
            assert client_as(eca_admin).put(URL, json=body).status_code == 422, field

    def test_an_unknown_material_is_a_404(self, client_as, eca_admin):
        r = _put(client_as, eca_admin, accepted_materials=["paper", "kryptonite"])
        assert r.status_code == 404 and r.json()["code"] == "material_not_found"
        assert client_as(eca_admin).get(URL).json()["accepted_materials"] is None

    def test_a_retired_material_is_refused(self, client_as, db, eca_admin):
        from app.domains.inventory.models import Material

        db.get(Material, "glass").is_active = False
        db.commit()
        r = _put(client_as, eca_admin, accepted_materials=["glass"])
        assert r.status_code == 400 and r.json()["code"] == "material_inactive"


class TestCapabilities:
    def test_me_announces_what_the_endpoints_enforce(self, client_as, db):
        for role, can_edit in (("eca_admin", True), ("eca_operator", False), ("eca_warehouse", False)):
            user = factories.make_user(db, "eca", role_code=role)
            caps = set(client_as(user).get("/auth/me").json()["capabilities"])
            assert "eca_routing_profile.view" in caps
            assert ("eca_routing_profile.edit" in caps) == can_edit == (_put(client_as, user).status_code == 200)
