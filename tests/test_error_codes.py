"""Every error response carries a stable machine-readable `code` next to `detail`."""
import pathlib
import re

MISSING_ID = "00000000-0000-0000-0000-000000000000"


def _error(response, status, code):
    assert response.status_code == status, response.text
    body = response.json()
    assert body["code"] == code
    assert "detail" in body


class TestErrorCodes:
    def test_bad_credentials(self, client):
        r = client.post("/auth/login", json={"email": "nobody@example.com", "password": "Wrong-pass-1"})
        _error(r, 401, "invalid_credentials")

    def test_missing_or_bad_token(self, client):
        r = client.get("/users/" + MISSING_ID, headers={"Authorization": "Bearer not-a-token"})
        _error(r, 401, "invalid_token")

    def test_not_found_uses_a_specific_code(self, client_as, eca_admin):
        _error(client_as(eca_admin).get(f"/weighings/{MISSING_ID}"), 404, "weighing_not_found")
        _error(client_as(eca_admin).get(f"/transactions/{MISSING_ID}"), 404, "transaction_not_found")

    def test_forbidden_default_and_specific(self, client_as, recycler, citizen):
        _error(client_as(recycler).get("/inventory"), 403, "forbidden")
        _error(client_as(recycler).get(f"/users/{citizen.id}"), 403, "forbidden")

    def test_validation_errors(self, client_as, eca_admin):
        r = client_as(eca_admin).post("/weighings", json={})
        _error(r, 422, "validation_error")
        assert isinstance(r.json()["detail"], list)  # the field-level list is still there

    def test_unknown_route_gets_a_generic_code(self, client):
        _error(client.get("/does-not-exist"), 404, "not_found")

    def test_wrong_method_gets_a_generic_code(self, client):
        _error(client.put("/auth/login"), 405, "method_not_allowed")

    def test_business_rule_code(self, client_as, eca_admin, recycler, warehouse):
        c = client_as(eca_admin)
        payload = {"recycler_id": str(recycler.id), "material_code": "plastic",
                   "warehouse_id": str(warehouse.id), "kg": "10", "price_per_kg": "500"}
        weighing = c.post("/weighings", json=payload).json()
        r = c.patch(f"/weighings/{weighing['id']}/status", json={"status": "paid"})
        _error(r, 400, "invalid_transition")


def _calls(source: str, name: str):
    """Yield the argument text of every `name(...)` call in `source`."""
    for match in re.finditer(name + r"\(", source):
        depth, i = 1, match.end()
        while depth:
            depth += (source[i] == "(") - (source[i] == ")")
            i += 1
        yield source[match.end():i - 1]


def test_no_endpoint_raises_a_bare_http_exception_with_a_message():
    """New errors must use ApiError (with a code), not a bare HTTPException carrying a detail."""
    app_dir = pathlib.Path(__file__).resolve().parent.parent / "app"
    offenders = sorted(
        str(path.relative_to(app_dir))
        for path in app_dir.rglob("*.py")
        if path.name != "errors.py"
        for call in _calls(path.read_text(encoding="utf-8"), "HTTPException")
        if "detail=" in call
    )
    assert not offenders, f"use ApiError(code, ...) instead of HTTPException in: {offenders}"
