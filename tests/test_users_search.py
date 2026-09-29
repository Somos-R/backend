"""GET /users?q= : case- and accent-insensitive "contains" over name, document and email."""
import pytest

from tests import factories


@pytest.fixture
def people(db):
    """Recyclers with accents, a shared surname and look-alike characters."""
    make = lambda **kw: factories.make_user(db, "recycler", **kw)  # noqa: E731
    return {
        "jose": make(full_name="José Pérez", id_number="1010", email="jose@correo.com"),
        "maria": make(full_name="María Pérez", id_number="2020", email="maria.p@correo.com"),
        "ana": make(full_name="Ana Gómez", id_number="3030", email="ana_g@correo.com"),
        "pct": make(full_name="Rebajas 50% Ltda", id_number="4040", email="rebajas@correo.com"),
    }


def _names(client, **params):
    r = client.get("/users", params=params)
    assert r.status_code == 200, r.text
    body = r.json()
    return sorted(item["full_name"] for item in body["items"]), body["total"]


class TestSearch:
    def test_ignores_case_and_accents(self, client_as, eca_admin, people):
        c = client_as(eca_admin)
        for q in ("perez", "PÉREZ", "pérez", "Perez"):
            assert _names(c, q=q)[0] == ["José Pérez", "María Pérez"]

    def test_the_query_may_carry_accents_the_data_lacks(self, client_as, eca_admin, db):
        factories.make_user(db, "recycler", full_name="Sofia Rios")
        assert _names(client_as(eca_admin), q="Sofía")[0] == ["Sofia Rios"]

    def test_is_a_contains_match(self, client_as, eca_admin, people):
        assert _names(client_as(eca_admin), q="ose P")[0] == ["José Pérez"]

    def test_matches_the_document_number(self, client_as, eca_admin, people):
        assert _names(client_as(eca_admin), q="2020")[0] == ["María Pérez"]

    def test_matches_the_email(self, client_as, eca_admin, people):
        assert _names(client_as(eca_admin), q="maria.p@")[0] == ["María Pérez"]

    def test_surrounding_spaces_are_ignored(self, client_as, eca_admin, people):
        assert _names(client_as(eca_admin), q="  gomez  ")[0] == ["Ana Gómez"]

    def test_no_match_is_an_empty_page_with_total_zero(self, client_as, eca_admin, people):
        assert _names(client_as(eca_admin), q="zzzz") == ([], 0)


class TestWildcardsAreLiteral:
    def test_a_percent_sign_only_matches_a_percent_sign(self, client_as, eca_admin, people):
        assert _names(client_as(eca_admin), q="50%")[0] == ["Rebajas 50% Ltda"]

    def test_a_lone_percent_pair_does_not_match_everything(self, client_as, eca_admin, people):
        assert _names(client_as(eca_admin), q="%%") == ([], 0)

    def test_an_underscore_is_not_a_single_character_wildcard(self, client_as, eca_admin, people):
        assert _names(client_as(eca_admin), q="ana_g")[0] == ["Ana Gómez"]  # matches the email
        assert _names(client_as(eca_admin), q="an_ G") == ([], 0)

    def test_a_backslash_does_not_break_the_query(self, client_as, eca_admin, people):
        assert _names(client_as(eca_admin), q="a\\b") == ([], 0)


class TestTooShort:
    @pytest.mark.parametrize("q", ["", " ", "a", " a "])
    def test_shorter_than_two_characters_is_ignored(self, client_as, eca_admin, people, q):
        names, total = _names(client_as(eca_admin), q=q)
        assert total >= 4 and "Ana Gómez" in names

    def test_more_than_100_characters_is_a_422(self, client_as, eca_admin):
        r = client_as(eca_admin).get("/users", params={"q": "x" * 101})
        assert r.status_code == 422


class TestCombinesWithOtherFilters:
    def test_with_verification_status(self, client_as, eca_admin, db, people):
        people["maria"].verification_status = "pending"
        db.commit()
        c = client_as(eca_admin)
        assert _names(c, q="perez", verification_status="verified")[0] == ["José Pérez"]
        assert _names(c, q="perez", verification_status="pending")[0] == ["María Pérez"]

    def test_total_reflects_the_whole_filter_not_the_page(self, client_as, eca_admin, people):
        r = client_as(eca_admin).get("/users", params={"q": "perez", "limit": 1})
        body = r.json()
        assert len(body["items"]) == 1 and body["total"] == 2

    def test_limit_one_gives_an_exact_count_cheaply(self, client_as, eca_admin, people):
        body = client_as(eca_admin).get("/users", params={"q": "correo.com", "limit": 1}).json()
        assert body["total"] == 4

    def test_paging_through_the_matches_never_repeats_a_row(self, client_as, eca_admin, people):
        c = client_as(eca_admin)
        seen = []
        for offset in (0, 1):
            seen += [i["id"] for i in c.get("/users", params={"q": "perez", "limit": 1, "offset": offset}).json()["items"]]
        assert len(seen) == len(set(seen)) == 2


class TestRespectsVisibility:
    def test_search_never_widens_what_the_role_may_see(self, client_as, db, people):
        operator = factories.make_user(db, "eca", role_code="eca_operator")
        factories.make_user(db, "citizen", full_name="Carlos Pérez Ciudadano")
        names, _ = _names(client_as(operator), q="perez")
        assert "Carlos Pérez Ciudadano" not in names and "José Pérez" in names

    def test_an_association_admin_may_find_any_type(self, client_as, association_admin, db):
        factories.make_user(db, "citizen", full_name="Carlos Pérez Ciudadano")
        assert "Carlos Pérez Ciudadano" in _names(client_as(association_admin), q="perez")[0]

    def test_roles_without_directory_access_still_get_403(self, client_as, recycler):
        assert client_as(recycler).get("/users", params={"q": "perez"}).status_code == 403
