"""What the API exposes over HTTP: health probes, security headers, docs, CORS, host check
and deployment warnings (tasks 4.1 - 4.5).

`create_app()` is called with tweaked settings so each environment can be checked without
restarting anything.
"""
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, settings
from app.core.database import get_db
from app.main import create_app

STRONG_KEY = "k" * 48
ORIGIN = "https://app.somosr.test"


@pytest.fixture
def build(monkeypatch):
    """Build an app after applying setting overrides, e.g. build(app_env="prod")."""
    def _build(**overrides) -> TestClient:
        for name, value in overrides.items():
            monkeypatch.setattr(settings, name, value)
        return TestClient(create_app())
    return _build


# --- Health probes ---------------------------------------------------------------------------

class BrokenSession:
    def execute(self, *args, **kwargs):
        raise RuntimeError("password authentication failed for user postgres")


class TestHealth:
    @pytest.mark.parametrize("path", ["/health", "/health/live"])
    def test_liveness(self, client, path):
        r = client.get(path)
        assert r.status_code == 200 and r.json() == {"status": "ok"}

    def test_readiness_ok_when_the_database_answers(self, client):
        r = client.get("/health/ready")
        assert r.status_code == 200 and r.json() == {"status": "ok"}

    def test_readiness_is_503_and_leaks_nothing_when_the_database_is_down(self, build):
        c = build()
        c.app.dependency_overrides[get_db] = lambda: BrokenSession()
        r = c.get("/health/ready")
        assert r.status_code == 503
        assert r.json() == {"status": "unavailable"}
        assert "password" not in r.text

    def test_liveness_never_touches_the_database(self, build):
        c = build()
        c.app.dependency_overrides[get_db] = lambda: pytest.fail("liveness must not use the database")
        assert c.get("/health/live").status_code == 200
        assert c.get("/health").status_code == 200


# --- Security headers -------------------------------------------------------------------------

class TestSecurityHeaders:
    def test_every_response_carries_the_base_headers(self, build):
        r = build().get("/health/live")
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["X-Frame-Options"] == "DENY"
        assert r.headers["Referrer-Policy"] == "no-referrer"
        assert r.headers["Content-Security-Policy"] == "default-src 'none'; frame-ancestors 'none'"
        assert "geolocation=()" in r.headers["Permissions-Policy"]

    def test_error_responses_have_them_too(self, build):
        r = build().get("/no-such-route")
        assert r.status_code == 404 and r.headers["X-Content-Type-Options"] == "nosniff"

    def test_auth_responses_are_never_cached(self, build):
        c = build()
        r = c.post("/auth/login", json={"email": "a@test.com", "password": "x"})
        assert r.headers["Cache-Control"] == "no-store"
        assert "no-store" not in c.get("/health/live").headers.get("Cache-Control", "")

    def test_hsts_is_off_in_dev(self, build):
        assert "Strict-Transport-Security" not in build(app_env="dev").get("/health/live").headers

    @pytest.mark.parametrize("env", ["staging", "prod"])
    def test_hsts_is_on_outside_dev(self, build, env):
        r = build(app_env=env).get("/health/live")
        assert r.headers["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains"

    def test_swagger_ui_is_not_blocked_by_the_csp(self, build):
        r = build(app_env="dev").get("/docs")
        assert r.status_code == 200
        assert "Content-Security-Policy" not in r.headers
        assert r.headers["X-Content-Type-Options"] == "nosniff"


# --- API docs ---------------------------------------------------------------------------------

class TestDocs:
    @pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
    def test_available_in_dev(self, build, path):
        assert build(app_env="dev").get(path).status_code == 200

    @pytest.mark.parametrize("env", ["staging", "prod"])
    @pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
    def test_hidden_outside_dev(self, build, env, path):
        assert build(app_env=env).get(path).status_code == 404

    def test_can_be_forced_on_or_off(self, build):
        assert build(app_env="prod", enable_docs=True).get("/docs").status_code == 200
        assert build(app_env="dev", enable_docs=False).get("/docs").status_code == 404


# --- CORS -------------------------------------------------------------------------------------

class TestCors:
    def _preflight(self, c, origin=ORIGIN, method="POST", headers="Authorization, Content-Type"):
        return c.options("/auth/login", headers={
            "Origin": origin, "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": headers})

    def test_a_listed_origin_is_allowed(self, build):
        c = build(cors_origins=f"{ORIGIN},https://other.test")
        assert c.get("/health/live", headers={"Origin": ORIGIN}).headers[
            "access-control-allow-origin"] == ORIGIN
        assert self._preflight(c).status_code == 200

    def test_an_unlisted_origin_is_not(self, build):
        c = build(cors_origins=ORIGIN)
        r = c.get("/health/live", headers={"Origin": "https://evil.test"})
        assert "access-control-allow-origin" not in r.headers
        assert self._preflight(c, origin="https://evil.test").status_code == 400

    def test_a_trailing_slash_in_the_setting_still_matches(self, build):
        c = build(cors_origins=f"{ORIGIN}/")
        assert c.get("/health/live", headers={"Origin": ORIGIN}).headers[
            "access-control-allow-origin"] == ORIGIN

    def test_only_the_needed_methods_and_headers(self, build):
        c = build(cors_origins=ORIGIN)
        assert self._preflight(c, headers="X-Anything").status_code == 400
        assert self._preflight(c, method="TRACE").status_code == 400
        allowed = self._preflight(c).headers["access-control-allow-methods"]
        assert "PATCH" in allowed and "TRACE" not in allowed and "*" not in allowed

    def test_credentials_flag_follows_the_setting(self, build):
        on = build(cors_origins=ORIGIN, cors_allow_credentials=True)
        off = build(cors_origins=ORIGIN, cors_allow_credentials=False)
        assert on.get("/health/live", headers={"Origin": ORIGIN}).headers[
            "access-control-allow-credentials"] == "true"
        assert "access-control-allow-credentials" not in off.get(
            "/health/live", headers={"Origin": ORIGIN}).headers


# --- Trusted hosts ----------------------------------------------------------------------------

class TestTrustedHosts:
    def test_any_host_by_default(self, build):
        assert build().get("/health/live", headers={"Host": "anything.test"}).status_code == 200

    def test_only_listed_hosts_when_configured(self, build):
        c = build(allowed_hosts="api.somosr.test, backend.up.railway.app")
        assert c.get("/health/live", headers={"Host": "api.somosr.test"}).status_code == 200
        assert c.get("/health/live", headers={"Host": "backend.up.railway.app"}).status_code == 200
        assert c.get("/health/live", headers={"Host": "evil.test"}).status_code == 400


# --- Environment-specific configuration ---------------------------------------------------------

def _settings(**kwargs):
    return Settings(_env_file=None, database_url="postgresql://x", secret_key=STRONG_KEY, **kwargs)


class TestDeploymentWarnings:
    def test_dev_is_never_warned(self):
        assert _settings().deployment_warnings() == []

    def test_a_default_prod_config_is_flagged_item_by_item(self):
        warnings = " | ".join(
            _settings(app_env="prod", email_backend="console").deployment_warnings())
        for expected in ("EMAIL_BACKEND=console", "in-memory", "ALLOWED_HOSTS=*",
                         "localhost origins", "FRONTEND_URL points to localhost", "SENTRY_DSN is empty"):
            assert expected in warnings

    def test_a_complete_prod_config_has_no_warnings(self):
        config = _settings(
            app_env="prod", email_backend="resend", email_api_key="re_x",
            rate_limit_storage_uri="redis://redis:6379", allowed_hosts="api.somosr.com",
            cors_origins="https://app.somosr.com", frontend_url="https://app.somosr.com",
            sentry_dsn="https://key@example.invalid/1", admin_allowed_cidrs="203.0.113.0/24")
        assert config.deployment_warnings() == []

    def test_the_app_logs_them_at_startup(self, build, caplog):
        with caplog.at_level("WARNING"):
            build(app_env="staging")
        assert "Deployment check (staging)" in caplog.text

    def test_setting_parsers(self):
        config = _settings(cors_origins=" https://a.test/ ,https://b.test,, ", allowed_hosts=" ")
        assert config.cors_origin_list == ["https://a.test", "https://b.test"]
        assert config.allowed_host_list == ["*"]

    def test_docs_default_depends_on_the_environment(self):
        assert _settings(app_env="dev").docs_enabled is True
        assert _settings(app_env="prod").docs_enabled is False
        assert _settings(app_env="prod", enable_docs=True).docs_enabled is True
