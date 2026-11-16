"""The public catalogs (GET /catalogs/*) that the registration form and the mobile app read without signing in.

They pin the response shape (a field more or less breaks the clients) and that only active entries are listed.
GET /catalogs/associations is covered in test_recycler_association.py (TestPublicDirectory: shape, approved only,
search, pagination and its rate limit).
"""
from app.core.permissions import PLATFORM_ROLES, ROLE_USER_TYPE
from app.domains.catalogs.models import DocumentType, Role


def _retire(db, model, code):
    db.get(model, code).is_active = False
    db.flush()


class TestDocumentTypes:
    def test_no_sign_in_is_needed_and_each_entry_is_code_and_label(self, client):
        r = client.get("/catalogs/document-types")
        assert r.status_code == 200
        assert r.json() and all(set(entry) == {"code", "label"} for entry in r.json())
        assert {"CC", "NIT"} <= {entry["code"] for entry in r.json()}

    def test_it_lists_every_active_type_and_only_those(self, client, db):
        _retire(db, DocumentType, "PA")
        codes = {entry["code"] for entry in client.get("/catalogs/document-types").json()}
        active = {t.code for t in db.query(DocumentType).filter(DocumentType.is_active.is_(True))}
        assert codes == active and "PA" not in codes


class TestRoles:
    def test_no_sign_in_is_needed_and_each_entry_says_its_actor_type(self, client):
        r = client.get("/catalogs/roles")
        assert r.status_code == 200
        assert r.json() and all(set(entry) == {"code", "label", "user_type_code"} for entry in r.json())
        assert all(entry["user_type_code"] == ROLE_USER_TYPE.get(entry["code"]) for entry in r.json())
        assert "eca_admin" in {entry["code"] for entry in r.json()}

    def test_a_retired_role_leaves_the_list(self, client, db):
        _retire(db, Role, "route_manager")
        codes = {entry["code"] for entry in client.get("/catalogs/roles").json()}
        assert "route_manager" not in codes and "association_admin" in codes

    def test_it_lists_every_active_client_role_and_no_platform_role(self, client, db):
        codes = {entry["code"] for entry in client.get("/catalogs/roles").json()}
        active = {r.code for r in db.query(Role).filter(Role.is_active.is_(True))}
        assert codes == active - PLATFORM_ROLES
