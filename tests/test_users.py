"""Characterization tests for /users."""
from app.domains.users.enums import VerificationStatus
from app.domains.users.models import User
from tests import factories

MISSING_ID = "00000000-0000-0000-0000-000000000000"


class TestListUsers:
    def test_lists_with_pagination_metadata(self, client_as, association_admin, db):
        factories.make_user(db, "citizen")
        r = client_as(association_admin).get("/users?limit=1&offset=0")
        assert r.status_code == 200
        body = r.json()
        assert body["limit"] == 1 and body["offset"] == 0
        assert body["total"] >= 2
        assert len(body["items"]) == 1

    def test_filter_by_user_type(self, client_as, association_admin, db):
        factories.make_user(db, "citizen")
        r = client_as(association_admin).get("/users?user_type_code=citizen")
        assert r.status_code == 200
        assert r.json()["items"]
        assert all(u["user_type_code"] == "citizen" for u in r.json()["items"])

    def test_limit_is_capped(self, client_as, eca_admin):
        assert client_as(eca_admin).get("/users?limit=501").status_code == 422


class TestGetUser:
    def test_found(self, client_as, association_admin, citizen):
        r = client_as(association_admin).get(f"/users/{citizen.id}")
        assert r.status_code == 200
        assert r.json()["email"] == citizen.email

    def test_not_found(self, client_as, eca_admin):
        assert client_as(eca_admin).get(f"/users/{MISSING_ID}").status_code == 404

    def test_invalid_uuid(self, client_as, eca_admin):
        assert client_as(eca_admin).get("/users/not-a-uuid").status_code == 422


class TestUpdateUser:
    def test_updates_only_sent_fields(self, client_as, citizen):
        r = client_as(citizen).patch(f"/users/{citizen.id}", json={"phone": "3110000000"})
        assert r.status_code == 200
        assert r.json()["phone"] == "3110000000"
        assert r.json()["full_name"] == citizen.full_name

    def test_not_found(self, client_as, eca_admin):
        r = client_as(eca_admin).patch(f"/users/{MISSING_ID}", json={"phone": "1"})
        assert r.status_code == 404

    def test_duplicate_tax_id_conflicts(self, client_as, db):
        a = factories.make_user(db, "b2b_client")
        b = factories.make_user(db, "b2b_client")
        r = client_as(b).patch(f"/users/{b.id}", json={"tax_id": a.tax_id})
        assert r.status_code == 409


class TestRecyclerVerification:
    @staticmethod
    def _pending(db):
        return factories.make_user(
            db, "recycler", verification_status=VerificationStatus.pending, password_hash="")

    def test_verify_records_audit_fields_and_emails_an_activation_link(
        self, client_as, association_admin, db, outbox
    ):
        recycler = self._pending(db)
        r = client_as(association_admin).patch(
            f"/users/{recycler.id}/verification-status", json={"status": "verified"})
        assert r.status_code == 200
        assert r.json()["verification_status"] == "verified"
        db.refresh(recycler)
        assert recycler.verified_by == association_admin.id
        assert recycler.verified_at is not None
        assert recycler.password_hash == ""  # no password until the recycler activates
        assert [m.to for m in outbox] == [recycler.email]
        assert "activate?token=" in outbox[0].body

    def test_reject_requires_reason(self, client_as, association_admin, db):
        recycler = self._pending(db)
        r = client_as(association_admin).patch(
            f"/users/{recycler.id}/verification-status", json={"status": "rejected"})
        assert r.status_code == 422

    def test_reject_with_reason(self, client_as, association_admin, db):
        recycler = self._pending(db)
        r = client_as(association_admin).patch(
            f"/users/{recycler.id}/verification-status",
            json={"status": "rejected", "rejection_reason": "Documento ilegible"})
        assert r.status_code == 200
        assert r.json()["rejection_reason"] == "Documento ilegible"

    def test_only_applies_to_recyclers(self, client_as, association_admin, citizen):
        r = client_as(association_admin).patch(
            f"/users/{citizen.id}/verification-status", json={"status": "verified"})
        assert r.status_code == 400

    def test_recycler_logs_in_only_after_activating(
        self, client_as, client, association_admin, db, outbox
    ):
        recycler = self._pending(db)
        client_as(association_admin).patch(
            f"/users/{recycler.id}/verification-status", json={"status": "verified"})
        token = outbox[0].body.split("token=")[1].split()[0]

        before = client.post("/auth/login", json={"email": recycler.email, "password": ""})
        assert before.status_code == 401

        assert client.post("/auth/activate", json={
            "token": token, "password": "Reciclaje-2026!"}).status_code == 200
        after = client.post("/auth/login", json={
            "email": recycler.email, "password": "Reciclaje-2026!"})
        assert after.status_code == 200

    def test_national_id_is_not_a_valid_password(self, client_as, client, association_admin, db):
        recycler = self._pending(db)
        client_as(association_admin).patch(
            f"/users/{recycler.id}/verification-status", json={"status": "verified"})
        db.expire_all()
        user = db.get(User, recycler.id)
        r = client.post("/auth/login", json={"email": user.email, "password": user.id_number})
        assert r.status_code == 401
