"""GET /auth/me: the signed-in profile plus the capabilities the server evaluates for it."""
import pytest

from app.core.permissions import CAPABILITIES, ROLE_USER_TYPE
from tests import factories

EXPECTED = {
    "eca_admin": {
        "recyclers.view", "weighings.view", "weighings.create", "weighings.review", "weighings.pay",
        "inventory.view", "inventory.edit", "transactions.view", "transactions.create",
        "transactions.manage", "transactions.pay",
    },
    "eca_operator": {
        "recyclers.view", "weighings.view", "weighings.create", "weighings.review",
        "inventory.view", "transactions.view",
    },
    "eca_warehouse": {
        "recyclers.view", "weighings.view", "inventory.view", "inventory.edit",
        "transactions.view", "transactions.create", "transactions.manage",
    },
    "association_admin": {
        "recyclers.view", "recyclers.verify", "weighings.view", "weighings.review", "weighings.pay",
        "inventory.view", "transactions.view", "transactions.pay", "audit.view",
    },
    "association_operator": {
        "recyclers.view", "recyclers.verify", "weighings.view", "weighings.review", "inventory.view",
    },
    "route_manager": {"recyclers.view"},
}


def _me(client_as, user):
    r = client_as(user).get("/auth/me")
    assert r.status_code == 200, r.text
    return r.json()


class TestCapabilities:
    @pytest.mark.parametrize("role", sorted(EXPECTED))
    def test_each_role_gets_exactly_its_capabilities(self, client_as, db, role):
        user = factories.make_user(db, ROLE_USER_TYPE[role], role_code=role)
        assert set(_me(client_as, user)["capabilities"]) == EXPECTED[role]

    def test_the_table_covers_every_role(self):
        assert set(EXPECTED) == set(ROLE_USER_TYPE)

    def test_every_capability_is_granted_to_someone(self):
        granted = set().union(*EXPECTED.values())
        assert granted == set(CAPABILITIES)

    @pytest.mark.parametrize("user_type", ["citizen", "building", "recycler", "b2b_client"])
    def test_actor_types_without_operations_get_nothing(self, client_as, db, user_type):
        assert _me(client_as, factories.make_user(db, user_type))["capabilities"] == []

    def test_staff_without_a_role_get_nothing(self, client_as, db):
        assert _me(client_as, factories.make_user(db, "eca"))["capabilities"] == []

    def test_a_role_that_does_not_fit_the_actor_type_gets_nothing(self, client_as, db):
        user = factories.make_user(db, "association", role_code="eca_admin")
        assert _me(client_as, user)["capabilities"] == []

    def test_the_role_comes_from_the_database_not_from_the_token(self, client_as, db):
        user = factories.make_user(db, "eca", role_code="eca_operator")
        client = client_as(user)  # token issued while the user was an operator
        user.role_code = "eca_admin"
        db.commit()
        assert "weighings.pay" in client.get("/auth/me").json()["capabilities"]

    def test_capabilities_match_what_the_endpoints_enforce(self, client_as, db):
        """Spot check: what /me promises is what the API then allows or refuses."""
        operator = factories.make_user(db, "eca", role_code="eca_operator")
        c = client_as(operator)
        caps = set(_me(client_as, operator)["capabilities"])
        assert ("inventory.view" in caps) == (c.get("/inventory").status_code == 200)
        assert ("transactions.view" in caps) == (c.get("/transactions").status_code == 200)
        assert "audit.view" not in caps and c.get("/audit-log").status_code == 403


class TestProfile:
    def test_returns_the_profile_of_the_caller(self, client_as, eca_admin):
        body = _me(client_as, eca_admin)
        assert body["id"] == str(eca_admin.id)
        assert body["email"] == eca_admin.email
        assert body["user_type_code"] == "eca" and body["role_code"] == "eca_admin"
        assert body["is_active"] is True

    def test_never_exposes_credentials(self, client_as, eca_admin):
        body = _me(client_as, eca_admin)
        assert not {"password_hash", "password", "token_version", "failed_login_attempts"} & set(body)

    def test_requires_authentication(self, client):
        assert client.get("/auth/me").status_code in (401, 403)

    def test_a_bad_token_is_a_401(self, client):
        r = client.get("/auth/me", headers={"Authorization": "Bearer nope"})
        assert r.status_code == 401 and r.json()["code"] == "invalid_token"

    def test_a_deactivated_account_is_refused(self, client_as, db):
        user = factories.make_user(db, "eca", role_code="eca_admin")
        client = client_as(user)
        user.is_active = False
        db.commit()
        assert client.get("/auth/me").status_code == 401
