"""Links between ECAs and Associations: the ECA asks, the Association decides, either can remove."""
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.rate_limit import limiter
from app.domains.audit.models import AuditLog
from app.domains.organizations.enums import LinkStatus, OrganizationStatus
from app.domains.organizations.models import EcaAssociationLink
from tests import factories

MISSING = "00000000-0000-0000-0000-000000000000"


@pytest.fixture
def w(db):
    def party(org_type, name, city):
        org = factories.make_organization(db, org_type, legal_name=name, city=city)
        role = f"{org_type}_admin"
        return SimpleNamespace(
            org=org,
            admin=factories.make_user(db, org_type, role_code=role, organization_id=org.id),
            operator=factories.make_user(
                db, org_type, role_code="eca_operator" if org_type == "eca" else "association_operator",
                organization_id=org.id),
        )

    return SimpleNamespace(
        eca_a=party("eca", "ECA Alfa", "Bogotá"), eca_b=party("eca", "ECA Beta", "Cali"),
        assoc_x=party("association", "Asociación Xenón", "Medellín"),
        assoc_y=party("association", "Asociación Ñandú", "Cúcuta"),
    )


def _request(client_as, eca, assoc):
    return client_as(eca.admin).post("/links", json={"association_id": str(assoc.org.id)})


def _link(client_as, eca, assoc):
    r = _request(client_as, eca, assoc)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _actions(db, link_id):
    rows = db.scalars(select(AuditLog).where(AuditLog.target_id == str(link_id)).order_by(AuditLog.occurred_at)).all()
    return [(r.action, r.details) for r in rows]


class TestDirectory:
    def test_an_eca_admin_sees_the_approved_associations_with_name_and_city_only(self, client_as, w):
        items = client_as(w.eca_a.admin).get("/directory/associations", params={"limit": 100}).json()["items"]
        mine = {i["id"]: i for i in items}
        assert set(mine[str(w.assoc_x.org.id)]) == {"id", "legal_name", "city", "link_status"}
        assert mine[str(w.assoc_x.org.id)]["legal_name"] == "Asociación Xenón"
        assert mine[str(w.assoc_x.org.id)]["city"] == "Medellín" and mine[str(w.assoc_x.org.id)]["link_status"] is None
        assert str(w.eca_b.org.id) not in mine  # ECAs are not in the directory

    def test_associations_that_are_not_approved_are_left_out(self, client_as, db, w):
        hidden = [factories.make_organization(db, "association", status=s, legal_name=f"Oculta {s.value}")
                  for s in (OrganizationStatus.draft, OrganizationStatus.submitted, OrganizationStatus.rejected,
                            OrganizationStatus.suspended)]
        ids = {i["id"] for i in client_as(w.eca_a.admin).get("/directory/associations", params={"limit": 100}).json()["items"]}
        assert not ids & {str(o.id) for o in hidden}

    def test_it_says_the_state_of_my_link_with_each(self, client_as, w):
        _link(client_as, w.eca_a, w.assoc_x)
        items = {i["id"]: i for i in client_as(w.eca_a.admin).get("/directory/associations", params={"limit": 100}).json()["items"]}
        assert items[str(w.assoc_x.org.id)]["link_status"] == "requested"
        assert items[str(w.assoc_y.org.id)]["link_status"] is None
        other = {i["id"]: i for i in client_as(w.eca_b.admin).get("/directory/associations", params={"limit": 100}).json()["items"]}
        assert other[str(w.assoc_x.org.id)]["link_status"] is None  # not someone else's link

    def test_search_ignores_case_and_accents(self, client_as, w):
        for q in ("xenon", "XENÓN", "nandu"):
            names = [i["legal_name"] for i in client_as(w.eca_a.admin).get("/directory/associations", params={"q": q}).json()["items"]]
            assert len(names) == 1
        assert client_as(w.eca_a.admin).get("/directory/associations", params={"q": "zzzz"}).json()["total"] == 0

    def test_a_short_query_is_ignored(self, client_as, w):
        assert client_as(w.eca_a.admin).get("/directory/associations", params={"q": "x"}).json()["total"] >= 2

    def test_pagination_is_stable(self, client_as, w):
        c = client_as(w.eca_a.admin)
        first = c.get("/directory/associations", params={"limit": 1, "offset": 0}).json()
        second = c.get("/directory/associations", params={"limit": 1, "offset": 1}).json()
        assert first["total"] == second["total"] and first["items"][0]["id"] != second["items"][0]["id"]

    @pytest.mark.parametrize("who", ["assoc_x.admin", "eca_a.operator", "assoc_x.operator"])
    def test_only_eca_admins_may_look(self, client_as, w, who):
        group, attr = who.split(".")
        assert client_as(getattr(getattr(w, group), attr)).get("/directory/associations").status_code == 403

    def test_recyclers_and_anonymous_callers_cannot(self, client_as, client, db):
        assert client_as(factories.make_user(db, "recycler")).get("/directory/associations").status_code == 403
        assert client.get("/directory/associations").status_code in (401, 403)

    def test_an_eca_that_is_not_approved_cannot(self, client_as, db):
        org = factories.make_organization(db, "eca", status=OrganizationStatus.submitted)
        admin = factories.make_user(db, "eca", role_code="eca_admin", organization_id=org.id)
        r = client_as(admin).get("/directory/associations")
        assert r.status_code == 403 and r.json()["code"] == "organization_not_active"

    def test_an_admin_without_an_organization_cannot(self, client_as, db):
        admin = factories.make_user(db, "eca", role_code="eca_admin", organization_id=None)
        assert client_as(admin).get("/directory/associations").json()["code"] == "no_organization"


class TestRequesting:
    def test_the_eca_asks_and_the_link_is_requested(self, client_as, db, w):
        r = _request(client_as, w.eca_a, w.assoc_x)
        assert r.status_code == 201
        body = r.json()
        assert body["status"] == "requested" and body["decided_at"] is None and body["rejection_reason"] is None
        assert body["eca"]["legal_name"] == "ECA Alfa" and body["association"]["legal_name"] == "Asociación Xenón"
        assert set(body["association"]) == {"id", "legal_name", "city"}  # no tax id, no contact data
        assert _actions(db, body["id"])[0][0] == "link.requested"

    def test_it_is_many_to_many(self, client_as, w):
        for eca, assoc in [(w.eca_a, w.assoc_x), (w.eca_a, w.assoc_y), (w.eca_b, w.assoc_x)]:
            assert _request(client_as, eca, assoc).status_code == 201

    def test_asking_twice_is_refused(self, client_as, w):
        _link(client_as, w.eca_a, w.assoc_x)
        r = _request(client_as, w.eca_a, w.assoc_x)
        assert r.status_code == 409 and r.json()["code"] == "link_already_requested"

    def test_an_active_link_cannot_be_requested_again(self, client_as, w):
        link_id = _link(client_as, w.eca_a, w.assoc_x)
        client_as(w.assoc_x.admin).post(f"/links/{link_id}/accept")
        r = _request(client_as, w.eca_a, w.assoc_x)
        assert r.status_code == 409 and r.json()["code"] == "link_already_active"

    def test_after_a_rejection_the_eca_may_ask_again(self, client_as, db, w):
        link_id = _link(client_as, w.eca_a, w.assoc_x)
        client_as(w.assoc_x.admin).post(f"/links/{link_id}/reject", json={"reason": "Sin cupo"})
        again = _request(client_as, w.eca_a, w.assoc_x)
        assert again.status_code == 201 and again.json()["id"] == link_id  # the same row is reopened
        assert again.json()["status"] == "requested" and again.json()["rejection_reason"] is None
        assert again.json()["decided_at"] is None
        assert db.scalars(select(EcaAssociationLink)).all().__len__() == 1

    def test_after_a_removal_the_eca_may_ask_again(self, client_as, w):
        link_id = _link(client_as, w.eca_a, w.assoc_x)
        client_as(w.eca_a.admin).post(f"/links/{link_id}/remove")
        assert _request(client_as, w.eca_a, w.assoc_x).status_code == 201

    def test_an_unknown_association_or_an_eca_id(self, client_as, w):
        c = client_as(w.eca_a.admin)
        assert c.post("/links", json={"association_id": MISSING}).json()["code"] == "organization_not_found"
        r = c.post("/links", json={"association_id": str(w.eca_b.org.id)})
        assert r.status_code == 404 and r.json()["code"] == "organization_not_found"

    def test_an_association_that_is_not_approved(self, client_as, db, w):
        org = factories.make_organization(db, "association", status=OrganizationStatus.submitted)
        r = client_as(w.eca_a.admin).post("/links", json={"association_id": str(org.id)})
        assert r.status_code == 409 and r.json()["code"] == "organization_not_active"

    @pytest.mark.parametrize("who", ["assoc_x.admin", "eca_a.operator", "assoc_x.operator"])
    def test_only_an_eca_admin_may_ask(self, client_as, w, who):
        group, attr = who.split(".")
        r = client_as(getattr(getattr(w, group), attr)).post("/links", json={"association_id": str(w.assoc_y.org.id)})
        assert r.status_code == 403

    def test_anonymous_callers_cannot(self, client, w):
        assert client.post("/links", json={"association_id": str(w.assoc_x.org.id)}).status_code in (401, 403)

    def test_malformed_input(self, client_as, w):
        assert client_as(w.eca_a.admin).post("/links", json={"association_id": "no-es-uuid"}).status_code == 422
        assert client_as(w.eca_a.admin).post("/links", json={}).status_code == 422

    def test_it_is_rate_limited(self, client_as, w, monkeypatch):
        monkeypatch.setattr(limiter, "enabled", True)
        monkeypatch.setattr(settings, "rate_limit_register", "2/minute")
        limiter.reset()
        try:
            statuses = [_request(client_as, w.eca_a, a).status_code for a in (w.assoc_x, w.assoc_y, w.assoc_x)]
            assert statuses == [201, 201, 429]
        finally:
            limiter.reset()


class TestListing:
    def test_each_side_sees_only_its_own_links(self, client_as, w):
        ax = _link(client_as, w.eca_a, w.assoc_x)
        _link(client_as, w.eca_a, w.assoc_y)
        bx = _link(client_as, w.eca_b, w.assoc_x)
        eca_a = {i["id"] for i in client_as(w.eca_a.admin).get("/links").json()["items"]}
        assoc_x = {i["id"] for i in client_as(w.assoc_x.admin).get("/links").json()["items"]}
        eca_b = {i["id"] for i in client_as(w.eca_b.admin).get("/links").json()["items"]}
        assert len(eca_a) == 2 and ax in eca_a and bx not in eca_a
        assert assoc_x == {ax, bx}
        assert eca_b == {bx}

    def test_the_total_counts_only_what_is_visible(self, client_as, w):
        _link(client_as, w.eca_a, w.assoc_x)
        _link(client_as, w.eca_b, w.assoc_x)
        _link(client_as, w.eca_b, w.assoc_y)
        assert client_as(w.assoc_y.admin).get("/links", params={"limit": 1}).json()["total"] == 1
        assert client_as(w.assoc_x.admin).get("/links", params={"limit": 1}).json()["total"] == 2

    def test_filter_by_status(self, client_as, w):
        first = _link(client_as, w.eca_a, w.assoc_x)
        _link(client_as, w.eca_a, w.assoc_y)
        client_as(w.assoc_x.admin).post(f"/links/{first}/accept")
        c = client_as(w.eca_a.admin)
        assert [i["id"] for i in c.get("/links", params={"status": "active"}).json()["items"]] == [first]
        assert c.get("/links", params={"status": "requested"}).json()["total"] == 1
        assert c.get("/links", params={"status": "nada"}).status_code == 422

    def test_the_counterpart_is_shown_by_name_and_city_only(self, client_as, w):
        _link(client_as, w.eca_a, w.assoc_x)
        item = client_as(w.assoc_x.admin).get("/links").json()["items"][0]
        assert set(item["eca"]) == {"id", "legal_name", "city"} and item["eca"]["legal_name"] == "ECA Alfa"

    def test_a_third_organization_sees_nothing(self, client_as, w):
        _link(client_as, w.eca_a, w.assoc_x)
        assert client_as(w.eca_b.admin).get("/links").json()["total"] == 0
        assert client_as(w.assoc_y.admin).get("/links").json()["total"] == 0

    @pytest.mark.parametrize("who", ["eca_a.operator", "assoc_x.operator"])
    def test_only_admins_may_look(self, client_as, w, who):
        group, attr = who.split(".")
        assert client_as(getattr(getattr(w, group), attr)).get("/links").status_code == 403


class TestDeciding:
    def test_the_association_accepts(self, client_as, db, w):
        link_id = _link(client_as, w.eca_a, w.assoc_x)
        r = client_as(w.assoc_x.admin).post(f"/links/{link_id}/accept")
        assert r.status_code == 200 and r.json()["status"] == "active" and r.json()["decided_at"] is not None
        row = db.get(EcaAssociationLink, link_id)
        assert row.decided_by == w.assoc_x.admin.id
        assert _actions(db, link_id)[-1] == ("link.accepted", {"eca_id": str(w.eca_a.org.id)})

    def test_the_association_rejects_with_or_without_a_reason(self, client_as, db, w):
        first = _link(client_as, w.eca_a, w.assoc_x)
        r = client_as(w.assoc_x.admin).post(f"/links/{first}/reject", json={"reason": "Sin cupo"})
        assert r.status_code == 200 and r.json()["status"] == "rejected" and r.json()["rejection_reason"] == "Sin cupo"
        second = _link(client_as, w.eca_b, w.assoc_x)
        r = client_as(w.assoc_x.admin).post(f"/links/{second}/reject")
        assert r.status_code == 200 and r.json()["rejection_reason"] is None

    def test_the_reason_is_audited_as_a_flag_not_as_text(self, client_as, db, w):
        link_id = _link(client_as, w.eca_a, w.assoc_x)
        client_as(w.assoc_x.admin).post(f"/links/{link_id}/reject", json={"reason": "Texto privado del motivo"})
        action, details = _actions(db, link_id)[-1]
        assert action == "link.rejected" and details["has_reason"] is True and "privado" not in str(details)

    def test_the_reason_is_bounded(self, client_as, w):
        link_id = _link(client_as, w.eca_a, w.assoc_x)
        assert client_as(w.assoc_x.admin).post(f"/links/{link_id}/reject", json={"reason": "x" * 201}).status_code == 422

    def test_only_the_association_it_was_asked_of_can_decide(self, client_as, w):
        link_id = _link(client_as, w.eca_a, w.assoc_x)
        other = client_as(w.assoc_y.admin).post(f"/links/{link_id}/accept")
        missing = client_as(w.assoc_y.admin).post(f"/links/{MISSING}/accept")
        assert other.status_code == 404 and other.json() == missing.json() and other.json()["code"] == "link_not_found"

    def test_the_eca_cannot_accept_its_own_request(self, client_as, w):
        link_id = _link(client_as, w.eca_a, w.assoc_x)
        assert client_as(w.eca_a.admin).post(f"/links/{link_id}/accept").status_code == 403
        assert client_as(w.eca_a.admin).post(f"/links/{link_id}/reject").status_code == 403

    def test_operators_cannot_decide(self, client_as, w):
        link_id = _link(client_as, w.eca_a, w.assoc_x)
        assert client_as(w.assoc_x.operator).post(f"/links/{link_id}/accept").status_code == 403

    def test_a_decision_can_only_be_made_once(self, client_as, w):
        link_id = _link(client_as, w.eca_a, w.assoc_x)
        client_as(w.assoc_x.admin).post(f"/links/{link_id}/accept")
        for action in ("accept", "reject"):
            r = client_as(w.assoc_x.admin).post(f"/links/{link_id}/{action}")
            assert r.status_code == 409 and r.json()["code"] == "link_not_pending"

    def test_accepting_needs_the_eca_to_still_be_active(self, client_as, db, w):
        link_id = _link(client_as, w.eca_a, w.assoc_x)
        w.eca_a.org.status = OrganizationStatus.suspended
        db.commit()
        r = client_as(w.assoc_x.admin).post(f"/links/{link_id}/accept")
        assert r.status_code == 409 and r.json()["code"] == "organization_not_active"


class TestRemoving:
    def _active(self, client_as, w):
        link_id = _link(client_as, w.eca_a, w.assoc_x)
        client_as(w.assoc_x.admin).post(f"/links/{link_id}/accept")
        return link_id

    @pytest.mark.parametrize("side", ["eca_a", "assoc_x"])
    def test_either_side_can_end_an_active_link(self, client_as, db, w, side):
        link_id = self._active(client_as, w)
        r = client_as(getattr(w, side).admin).post(f"/links/{link_id}/remove")
        assert r.status_code == 200 and r.json()["status"] == "removed"
        assert _actions(db, link_id)[-1] == ("link.removed", {"from": "active", "by": {"eca_a": "eca", "assoc_x": "association"}[side]})

    def test_the_row_is_kept(self, client_as, db, w):
        link_id = self._active(client_as, w)
        client_as(w.eca_a.admin).post(f"/links/{link_id}/remove")
        assert db.get(EcaAssociationLink, link_id).status == LinkStatus.removed

    def test_the_eca_can_cancel_an_unanswered_request(self, client_as, w):
        link_id = _link(client_as, w.eca_a, w.assoc_x)
        assert client_as(w.eca_a.admin).post(f"/links/{link_id}/remove").json()["status"] == "removed"

    def test_the_association_cannot_remove_a_pending_request_it_should_reject_it(self, client_as, w):
        link_id = _link(client_as, w.eca_a, w.assoc_x)
        r = client_as(w.assoc_x.admin).post(f"/links/{link_id}/remove")
        assert r.status_code == 409 and r.json()["code"] == "link_not_removable"

    @pytest.mark.parametrize("final", ["reject", "remove"])
    def test_finished_links_cannot_be_removed(self, client_as, w, final):
        link_id = _link(client_as, w.eca_a, w.assoc_x)
        if final == "reject":
            client_as(w.assoc_x.admin).post(f"/links/{link_id}/reject")
        else:
            client_as(w.eca_a.admin).post(f"/links/{link_id}/remove")
        r = client_as(w.eca_a.admin).post(f"/links/{link_id}/remove")
        assert r.status_code == 409 and r.json()["code"] == "link_not_removable"

    def test_a_third_organization_gets_a_404(self, client_as, w):
        link_id = self._active(client_as, w)
        assert client_as(w.eca_b.admin).post(f"/links/{link_id}/remove").status_code == 404
        assert client_as(w.assoc_y.admin).post(f"/links/{link_id}/remove").status_code == 404

    def test_operators_cannot(self, client_as, w):
        link_id = self._active(client_as, w)
        assert client_as(w.eca_a.operator).post(f"/links/{link_id}/remove").status_code == 403

    def test_after_a_removal_the_directory_shows_it(self, client_as, w):
        link_id = self._active(client_as, w)
        client_as(w.assoc_x.admin).post(f"/links/{link_id}/remove")
        items = {i["id"]: i for i in client_as(w.eca_a.admin).get("/directory/associations", params={"limit": 100}).json()["items"]}
        assert items[str(w.assoc_x.org.id)]["link_status"] == "removed"


class TestConstraintsAndCapabilities:
    def test_a_link_needs_two_different_organizations(self, db, w):
        from sqlalchemy.exc import IntegrityError

        db.add(EcaAssociationLink(eca_id=w.eca_a.org.id, association_id=w.eca_a.org.id))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

    def test_one_row_per_pair(self, db, w):
        from sqlalchemy.exc import IntegrityError

        db.add(EcaAssociationLink(eca_id=w.eca_a.org.id, association_id=w.assoc_x.org.id))
        db.commit()
        db.add(EcaAssociationLink(eca_id=w.eca_a.org.id, association_id=w.assoc_x.org.id))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

    def test_the_capabilities_are_announced_to_the_right_roles(self, client_as, w):
        eca = set(client_as(w.eca_a.admin).get("/auth/me").json()["capabilities"])
        assoc = set(client_as(w.assoc_x.admin).get("/auth/me").json()["capabilities"])
        assert {"links.request", "links.view"} <= eca and "links.decide" not in eca
        assert {"links.decide", "links.view"} <= assoc and "links.request" not in assoc
        for operator in (w.eca_a.operator, w.assoc_x.operator):
            caps = set(client_as(operator).get("/auth/me").json()["capabilities"])
            assert not {"links.request", "links.decide", "links.view"} & caps
