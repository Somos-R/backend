"""An ECA receives material whoever brings it (ECA-05): a recycler of any association or none, or a person
who is not registered. What changes is where the weighing goes afterwards: only `linked` ones reach an
association.
"""
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.rate_limit import limiter
from app.domains.audit.models import AuditLog
from app.domains.inventory.models import Warehouse
from app.domains.organizations.enums import LinkStatus
from app.domains.organizations.models import EcaAssociationLink
from app.domains.weighings.models import AffiliationStatus, Weighing
from tests import factories


def _party(db, org_type, name):
    org = factories.make_organization(db, org_type, legal_name=name, city="Bogotá")
    roles = ("eca_admin", "eca_operator", "eca_warehouse") if org_type == "eca" else (
        "association_admin", "association_operator", "route_manager")
    return SimpleNamespace(org=org, **{
        key: factories.make_user(db, org_type, role_code=role, organization_id=org.id)
        for key, role in zip(("admin", "operator", "extra"), roles)})


@pytest.fixture
def w(db):
    eca, other_eca = _party(db, "eca", "ECA Alfa"), _party(db, "eca", "ECA Beta")
    x, y = _party(db, "association", "Asociación Xenón"), _party(db, "association", "Asociación Yodo")
    wh = db.query(Warehouse).order_by(Warehouse.name).all()
    wh[0].organization_id, wh[1].organization_id = eca.org.id, other_eca.org.id
    db.add(EcaAssociationLink(eca_id=eca.org.id, association_id=x.org.id, status=LinkStatus.active))
    db.commit()
    return SimpleNamespace(
        eca=eca, other_eca=other_eca, x=x, y=y, wh=wh[0],
        linked=factories.make_user(db, "recycler", organization_id=x.org.id),
        stranger=factories.make_user(db, "recycler", organization_id=y.org.id, full_name="De Otra Asociación"),
        loose=factories.make_user(db, "recycler", organization_id=None, full_name="Independiente Suelto"),
    )


def _weigh(client_as, w, **who):
    body = {"material_code": "plastic", "warehouse_id": str(w.wh.id), "kg": "10", "price_per_kg": "400", **who}
    return client_as(w.eca.operator).post("/weighings", json=body)


def _walk_in(name="Ana Vendedora", id_number="52123456", id_type="CC"):
    return {"seller": {"full_name": name, "id_type": id_type, "id_number": id_number}}


def _ids(response):
    assert response.status_code == 200, response.text
    return {i["id"] for i in response.json()["items"]}


class TestWhoCanBeWeighed:
    def test_a_person_who_is_not_registered_is_identified_by_name_and_document(self, client_as, db, w):
        r = _weigh(client_as, w, **_walk_in())
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["recycler_id"] is None and body["recycler"] is None
        assert (body["seller_name"], body["seller_id_type"], body["seller_id_number"]) == ("Ana Vendedora", "CC", "52123456")
        assert body["affiliation_status"] == "independent"

    def test_a_recycler_of_an_association_linked_to_the_eca_is_linked(self, client_as, w):
        assert _weigh(client_as, w, recycler_id=str(w.linked.id)).json()["affiliation_status"] == "linked"

    def test_a_recycler_of_another_association_is_received_as_unlinked(self, client_as, w):
        r = _weigh(client_as, w, recycler_id=str(w.stranger.id))
        assert r.status_code == 201 and r.json()["affiliation_status"] == "unlinked_association"

    def test_a_recycler_without_an_association_is_independent(self, client_as, w):
        assert _weigh(client_as, w, recycler_id=str(w.loose.id)).json()["affiliation_status"] == "independent"

    def test_a_recycler_of_a_linked_association_who_is_not_verified_is_not_linked(self, client_as, db, w):
        from app.domains.users.enums import VerificationStatus

        pending = factories.make_user(db, "recycler", organization_id=w.x.org.id,
                                      verification_status=VerificationStatus.pending)
        assert _weigh(client_as, w, recycler_id=str(pending.id)).json()["affiliation_status"] == "unlinked_association"

    def test_only_a_deactivated_account_is_refused(self, client_as, db, w):
        inactive = factories.make_user(db, "recycler", organization_id=w.x.org.id, is_active=False)
        r = _weigh(client_as, w, recycler_id=str(inactive.id))
        assert r.status_code == 400 and r.json()["code"] == "recycler_inactive"

    def test_exactly_one_way_of_saying_who(self, client_as, w):
        both = _weigh(client_as, w, recycler_id=str(w.linked.id), **_walk_in())
        neither = _weigh(client_as, w)
        assert both.status_code == 422 and neither.status_code == 422

    def test_the_person_needs_a_valid_name_and_document(self, client_as, w):
        assert _weigh(client_as, w, **_walk_in(id_type="ZZZ")).json()["code"] == "invalid_id_type"
        assert _weigh(client_as, w, **_walk_in(name="A")).status_code == 422
        assert _weigh(client_as, w, **_walk_in(id_number="1")).status_code == 422
        assert _weigh(client_as, w, **_walk_in(id_number="1" * 21)).status_code == 422

    def test_a_unknown_recycler_id_is_still_a_404(self, client_as, w):
        r = _weigh(client_as, w, recycler_id="00000000-0000-0000-0000-000000000000")
        assert r.status_code == 404 and r.json()["code"] == "recycler_not_found"

    def test_the_database_refuses_a_weighing_nobody_delivered(self, db, w):
        from decimal import Decimal

        db.add(Weighing(material_code="plastic", warehouse_id=w.wh.id, kg=Decimal("1"), price_per_kg=Decimal("1"),
                        affiliation_status=AffiliationStatus.independent))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


class TestWhatReachesTheEca:
    def test_every_kind_appears_in_the_ecas_list_and_statistics(self, client_as, w):
        ids = {_weigh(client_as, w, **who).json()["id"] for who in (
            {"recycler_id": str(w.linked.id)}, {"recycler_id": str(w.stranger.id)},
            {"recycler_id": str(w.loose.id)}, _walk_in())}
        assert _ids(client_as(w.eca.admin).get("/weighings", params={"limit": 100})) == ids
        assert client_as(w.eca.admin).get("/weighings/stats").json()["total_weighings_month"] == 4

    def test_filtering_by_affiliation(self, client_as, w):
        _weigh(client_as, w, recycler_id=str(w.linked.id))
        loose = _weigh(client_as, w, recycler_id=str(w.loose.id)).json()["id"]
        walk_in = _weigh(client_as, w, **_walk_in()).json()["id"]
        got = _ids(client_as(w.eca.admin).get("/weighings", params={"affiliation": "independent"}))
        assert got == {loose, walk_in}
        assert client_as(w.eca.admin).get("/weighings", params={"affiliation": "nada"}).status_code == 422

    def test_validating_feeds_the_ecas_inventory_and_creates_a_purchase_without_a_recycler(self, client_as, w):
        wid = _weigh(client_as, w, **_walk_in()).json()["id"]
        assert client_as(w.eca.admin).patch(f"/weighings/{wid}/status", json={"status": "validated"}).status_code == 200
        assert client_as(w.eca.admin).get("/inventory").json()["total"] == 1
        purchases = client_as(w.eca.admin).get("/transactions", params={"type": "purchase"}).json()["items"]
        assert len(purchases) == 1 and purchases[0]["recycler"] is None and purchases[0]["weighing_id"] == wid

    def test_the_eca_pays_the_purchase_of_a_person_who_is_not_registered(self, client_as, w):
        wid = _weigh(client_as, w, **_walk_in()).json()["id"]
        client_as(w.eca.admin).patch(f"/weighings/{wid}/status", json={"status": "validated"})
        tx = client_as(w.eca.admin).get("/transactions").json()["items"][0]["id"]
        assert client_as(w.eca.admin).patch(f"/transactions/{tx}/status", json={"status": "paid"}).status_code == 200
        assert client_as(w.eca.admin).patch(f"/weighings/{wid}/status", json={"status": "paid"}).status_code == 200

    def test_the_recycler_still_sees_their_own_weighing_wherever_it_was_made(self, client_as, w):
        wid = _weigh(client_as, w, recycler_id=str(w.stranger.id)).json()["id"]
        assert _ids(client_as(w.stranger).get("/weighings")) == {wid}


class TestWhatDoesNotReachAnAssociation:
    def test_a_weighing_of_its_recycler_at_an_eca_it_is_not_linked_to_stays_with_that_eca(self, client_as, w):
        wid = _weigh(client_as, w, recycler_id=str(w.stranger.id)).json()["id"]  # Yodo's recycler, ECA Alfa
        for user in (w.y.admin, w.y.operator):
            assert _ids(client_as(user).get("/weighings")) == set()
            assert client_as(user).get(f"/weighings/{wid}").status_code == 404
            assert client_as(user).patch(f"/weighings/{wid}/status", json={"status": "validated"}).status_code == 404
        assert client_as(w.y.admin).get("/weighings/stats").json()["pending_count"] == 0

    def test_nor_does_its_purchase(self, client_as, w):
        wid = _weigh(client_as, w, recycler_id=str(w.stranger.id)).json()["id"]
        client_as(w.eca.admin).patch(f"/weighings/{wid}/status", json={"status": "validated"})
        tx = client_as(w.eca.admin).get("/transactions").json()["items"][0]["id"]
        assert client_as(w.y.admin).get("/transactions").json()["total"] == 0
        assert client_as(w.y.admin).get(f"/transactions/{tx}").status_code == 404
        assert client_as(w.y.admin).patch(f"/transactions/{tx}/status", json={"status": "paid"}).status_code == 404
        assert client_as(w.y.admin).get("/transactions/stats").json()["total_purchases_month"] == 0

    def test_nothing_of_an_independent_or_a_walk_in_reaches_any_association(self, client_as, w):
        _weigh(client_as, w, recycler_id=str(w.loose.id))
        _weigh(client_as, w, **_walk_in())
        for assoc in (w.x, w.y):
            assert client_as(assoc.admin).get("/weighings").json()["total"] == 0

    def test_a_linked_weighing_does_reach_its_association(self, client_as, w):
        wid = _weigh(client_as, w, recycler_id=str(w.linked.id)).json()["id"]
        assert _ids(client_as(w.x.admin).get("/weighings")) == {wid}
        client_as(w.eca.admin).patch(f"/weighings/{wid}/status", json={"status": "validated"})
        assert client_as(w.x.admin).get("/transactions").json()["total"] == 1

    def test_the_link_being_removed_later_does_not_take_back_what_was_already_delivered_through_it(
        self, client_as, db, w
    ):
        wid = _weigh(client_as, w, recycler_id=str(w.linked.id)).json()["id"]
        db.scalars(select(EcaAssociationLink)).one().status = LinkStatus.removed
        db.commit()
        assert _ids(client_as(w.x.admin).get("/weighings")) == {wid}

    def test_and_a_weighing_after_the_link_ends_no_longer_reaches_it(self, client_as, db, w):
        db.scalars(select(EcaAssociationLink)).one().status = LinkStatus.removed
        db.commit()
        _weigh(client_as, w, recycler_id=str(w.linked.id))
        assert client_as(w.x.admin).get("/weighings").json()["total"] == 0

    def test_another_eca_never_sees_any_of_it(self, client_as, w):
        _weigh(client_as, w, **_walk_in())
        assert client_as(w.other_eca.admin).get("/weighings").json()["total"] == 0


class TestAudit:
    def test_a_walk_in_is_recorded_without_their_name_or_document(self, client_as, db, w):
        _weigh(client_as, w, **_walk_in(name="Nombre Privado", id_number="99887766"))
        entry = db.scalars(select(AuditLog).where(AuditLog.action == "weighing.created")).one()
        assert entry.details["walk_in"] is True and entry.details["affiliation"] == "independent"
        assert entry.details["recycler_id"] is None
        assert "Privado" not in str(entry.details) and "99887766" not in str(entry.details)


class TestLookup:
    def _lookup(self, client_as, user, **params):
        return client_as(user).get("/recyclers/lookup", params=params)

    def test_it_finds_a_recycler_of_any_association_and_says_how_they_relate_to_my_eca(self, client_as, w):
        for who, affiliation, has_assoc in ((w.linked, "linked", True), (w.stranger, "unlinked_association", True),
                                            (w.loose, "independent", False)):
            r = self._lookup(client_as, w.eca.operator, document=who.id_number)
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["id"] == str(who.id) and body["affiliation"] == affiliation
            assert (body["association"] is not None) == has_assoc

    def test_it_returns_only_what_is_needed_to_weigh(self, client_as, w):
        body = self._lookup(client_as, w.eca.admin, document=w.linked.id_number).json()
        assert set(body) == {"id", "full_name", "id_type", "id_number", "is_active", "verification_status",
                             "association", "affiliation"}
        assert set(body["association"]) == {"id", "legal_name", "city"}

    def test_it_is_relative_to_the_asking_eca(self, client_as, w):
        assert self._lookup(client_as, w.other_eca.admin, document=w.linked.id_number).json()["affiliation"] == "unlinked_association"

    def test_the_document_is_matched_exactly(self, client_as, w):
        assert self._lookup(client_as, w.eca.admin, document=w.linked.id_number[:-1]).status_code == 404

    def test_unknown_and_wrong_type(self, client_as, w):
        assert self._lookup(client_as, w.eca.admin, document="00000000").json()["code"] == "recycler_not_found"
        assert self._lookup(client_as, w.eca.admin, document=w.linked.id_number, id_type="XX").status_code == 404
        assert self._lookup(client_as, w.eca.admin, document=w.linked.id_number, id_type=w.linked.id_type).status_code == 200

    def test_only_a_registered_recycler_is_found(self, client_as, db, w):
        citizen = factories.make_user(db, "citizen")
        assert self._lookup(client_as, w.eca.admin, document=citizen.id_number).status_code == 404

    @pytest.mark.parametrize("who", ["eca.extra", "x.admin", "x.operator"])
    def test_only_who_registers_weighings_may_look(self, client_as, w, who):
        group, attr = who.split(".")
        r = self._lookup(client_as, getattr(getattr(w, group), attr), document=w.linked.id_number)
        assert r.status_code == 403

    def test_an_account_without_an_organization_cannot(self, client_as, db, w):
        orphan = factories.make_user(db, "eca", role_code="eca_operator", organization_id=None)
        assert self._lookup(client_as, orphan, document=w.linked.id_number).json()["code"] == "no_organization"

    def test_short_or_missing_documents_are_a_validation_error(self, client_as, w):
        assert self._lookup(client_as, w.eca.admin, document="1").status_code == 422
        assert client_as(w.eca.admin).get("/recyclers/lookup").status_code == 422

    def test_it_is_rate_limited(self, client_as, w, monkeypatch):
        monkeypatch.setattr(limiter, "enabled", True)
        monkeypatch.setattr(settings, "rate_limit_register", "2/minute")
        limiter.reset()
        try:
            codes = [self._lookup(client_as, w.eca.admin, document=w.linked.id_number).status_code for _ in range(3)]
            assert codes == [200, 200, 429]
        finally:
            limiter.reset()
