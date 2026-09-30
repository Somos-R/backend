"""GET /weighings?q= : find who delivered, registered or not, without widening what the actor may see."""
from types import SimpleNamespace

import pytest

from app.domains.inventory.models import Warehouse
from app.domains.organizations.enums import LinkStatus
from app.domains.organizations.models import EcaAssociationLink
from tests import factories


def _party(db, org_type, name):
    org = factories.make_organization(db, org_type, legal_name=name)
    roles = ("eca_admin", "eca_operator") if org_type == "eca" else ("association_admin", "association_operator")
    return SimpleNamespace(org=org, **{
        key: factories.make_user(db, org_type, role_code=role, organization_id=org.id)
        for key, role in zip(("admin", "operator"), roles)})


@pytest.fixture
def w(db):
    eca, other = _party(db, "eca", "ECA Alfa"), _party(db, "eca", "ECA Beta")
    assoc = _party(db, "association", "Asociación Xenón")
    wh = db.query(Warehouse).order_by(Warehouse.name).all()
    wh[0].organization_id, wh[1].organization_id = eca.org.id, other.org.id
    db.add(EcaAssociationLink(eca_id=eca.org.id, association_id=assoc.org.id, status=LinkStatus.active))
    db.commit()
    return SimpleNamespace(
        eca=eca, other=other, assoc=assoc, wh=wh[0], wh_other=wh[1],
        maria=factories.make_user(db, "recycler", organization_id=assoc.org.id, full_name="María Pérez", id_number="10101010"),
        jose=factories.make_user(db, "recycler", organization_id=None, full_name="José Gómez", id_number="20202020"),
    )


def _weigh(client_as, user, warehouse, **who):
    body = {"material_code": "plastic", "warehouse_id": str(warehouse.id), "kg": "10", "price_per_kg": "400", **who}
    r = client_as(user).post("/weighings", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _seller(name, number):
    return {"seller": {"full_name": name, "id_type": "CC", "id_number": number}}


def _search(client_as, user, q):
    r = client_as(user).get("/weighings", params={"q": q, "limit": 100})
    assert r.status_code == 200, r.text
    return {i["id"] for i in r.json()["items"]}, r.json()["total"]


@pytest.fixture
def three(client_as, w):
    return SimpleNamespace(
        maria=_weigh(client_as, w.eca.operator, w.wh, recycler_id=str(w.maria.id)),
        jose=_weigh(client_as, w.eca.operator, w.wh, recycler_id=str(w.jose.id)),
        ana=_weigh(client_as, w.eca.operator, w.wh, **_seller("Ana Vendedora", "52123456")),
    )


class TestSearch:
    def test_by_the_name_of_a_registered_recycler(self, client_as, w, three):
        assert _search(client_as, w.eca.admin, "maría")[0] == {three.maria}

    def test_by_the_document_of_a_registered_recycler(self, client_as, w, three):
        assert _search(client_as, w.eca.admin, "2020")[0] == {three.jose}

    def test_by_the_name_of_an_unregistered_seller(self, client_as, w, three):
        assert _search(client_as, w.eca.admin, "vendedora")[0] == {three.ana}

    def test_by_the_document_of_an_unregistered_seller(self, client_as, w, three):
        assert _search(client_as, w.eca.admin, "52123")[0] == {three.ana}

    def test_it_ignores_case_and_accents(self, client_as, w, three):
        for q in ("MARIA", "maria perez", "María", "PÉREZ"):
            assert _search(client_as, w.eca.admin, q)[0] == {three.maria}
        assert _search(client_as, w.eca.admin, "GOMEZ")[0] == {three.jose}

    def test_a_short_query_is_ignored(self, client_as, w, three):
        assert _search(client_as, w.eca.admin, "m")[0] == {three.maria, three.jose, three.ana}

    def test_nothing_found_is_an_empty_page_with_total_zero(self, client_as, w, three):
        assert _search(client_as, w.eca.admin, "zzzz") == (set(), 0)

    def test_wildcards_are_plain_text(self, client_as, w, three):
        assert _search(client_as, w.eca.admin, "%%")[1] == 0
        assert _search(client_as, w.eca.admin, "a_i")[1] == 0
        assert _search(client_as, w.eca.admin, "a\\b")[1] == 0

    def test_it_combines_with_the_other_filters_and_the_total_is_exact(self, client_as, w, three):
        r = client_as(w.eca.admin).get("/weighings", params={"q": "a", "affiliation": "independent", "limit": 1})
        assert r.status_code == 200
        r = client_as(w.eca.admin).get("/weighings", params={"q": "ana", "affiliation": "independent", "limit": 1})
        assert r.json()["total"] == 1 and r.json()["items"][0]["id"] == three.ana
        r = client_as(w.eca.admin).get("/weighings", params={"q": "ana", "affiliation": "linked"})
        assert r.json()["total"] == 0

    def test_too_long_is_a_validation_error(self, client_as, w):
        assert client_as(w.eca.admin).get("/weighings", params={"q": "x" * 101}).status_code == 422


class TestItNeverWidensTheScope:
    def test_another_eca_finds_nothing(self, client_as, w, three):
        for q in ("maría", "vendedora", "52123456", "2020"):
            assert _search(client_as, w.other.admin, q) == (set(), 0)

    def test_an_association_only_finds_what_reached_it(self, client_as, w, three):
        # María is its recycler and her weighing was linked; José and Ana never reached it.
        assert _search(client_as, w.assoc.admin, "maría")[0] == {three.maria}
        assert _search(client_as, w.assoc.admin, "gómez") == (set(), 0)
        assert _search(client_as, w.assoc.admin, "vendedora") == (set(), 0)

    def test_a_recycler_searching_only_gets_their_own(self, client_as, w, three):
        assert _search(client_as, w.maria, "maría")[0] == {three.maria}
        assert _search(client_as, w.maria, "gómez") == (set(), 0)

    def test_staff_without_an_organization_find_nothing(self, client_as, db, w, three):
        orphan = factories.make_user(db, "eca", role_code="eca_operator", organization_id=None)
        assert _search(client_as, orphan, "maría") == (set(), 0)

    def test_the_same_seller_at_two_ecas_stays_separate(self, client_as, w, three):
        theirs = _weigh(client_as, w.other.operator, w.wh_other, **_seller("Ana Vendedora", "52123456"))
        assert _search(client_as, w.eca.admin, "vendedora")[0] == {three.ana}
        assert _search(client_as, w.other.admin, "vendedora")[0] == {theirs}
