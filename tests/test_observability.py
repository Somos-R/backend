"""Structured logging, request ids, access log, metrics and Sentry (task 4.7)."""
import io
import json
import logging
import sys

import pytest
import sentry_sdk
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from app.core import sentry as sentry_module
from app.core.config import Settings, settings
from app.core.context import get_request_id
from app.core.database import get_db
from app.core.sentry import FILTERED, init_sentry, scrub_event
from app.core.structured_logging import JsonFormatter, configure_logging
from app.main import create_app
from tests import factories
from tests.factories import DEFAULT_PASSWORD

METRICS_TOKEN = "m" * 32


@pytest.fixture
def api(monkeypatch):
    """A freshly built app (errors become 500 responses, as in production)."""
    def _build(**overrides) -> TestClient:
        for name, value in overrides.items():
            monkeypatch.setattr(settings, name, value)
        return TestClient(create_app(), raise_server_exceptions=False)
    return _build


def _access_records(caplog):
    return [r for r in caplog.records if r.name == "app.access"]


def _sample(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


# --- JSON formatter -----------------------------------------------------------------------

class TestJsonFormatter:
    @staticmethod
    def _record(message="hello", level=logging.INFO, exc_info=None, **extra):
        record = logging.LogRecord("app.test", level, __file__, 10, message, (), exc_info)
        for key, value in extra.items():
            setattr(record, key, value)
        return record

    def test_one_valid_json_object_with_the_core_fields(self):
        payload = json.loads(JsonFormatter().format(self._record("hello %s")))
        assert payload["message"] == "hello %s"
        assert payload["level"] == "INFO" and payload["logger"] == "app.test"
        assert payload["timestamp"].endswith("+00:00")

    def test_extra_fields_are_exported_and_none_is_dropped(self):
        payload = json.loads(JsonFormatter().format(self._record(status=404, user_id=None, route="/x")))
        assert payload["status"] == 404 and payload["route"] == "/x"
        assert "user_id" not in payload

    def test_arguments_are_interpolated(self):
        record = logging.LogRecord("t", logging.INFO, __file__, 1, "%s -> %s", ("GET", 200), None)
        assert json.loads(JsonFormatter().format(record))["message"] == "GET -> 200"

    def test_exceptions_include_the_traceback(self):
        try:
            raise ValueError("boom")
        except ValueError:
            payload = json.loads(JsonFormatter().format(self._record(exc_info=sys.exc_info())))
        assert "ValueError: boom" in payload["exception"] and "Traceback" in payload["exception"]

    def test_unserializable_values_do_not_break_logging(self):
        payload = json.loads(JsonFormatter().format(self._record(thing=object())))
        assert payload["thing"].startswith("<object object")

    def test_non_ascii_is_kept_readable(self):
        assert "Contraseña" in JsonFormatter().format(self._record("Contraseña incorrecta"))


class TestConfigureLogging:
    @pytest.fixture(autouse=True)
    def restore(self):
        root = logging.getLogger()
        handlers, level = list(root.handlers), root.level
        yield
        root.handlers[:] = handlers
        root.setLevel(level)

    def test_json_output_carries_the_request_id(self):
        stream = io.StringIO()
        configure_logging("INFO", json_logs=True, stream=stream)
        logging.getLogger("app.x").info("hola", extra={"request_id": "abc123"})
        line = json.loads(stream.getvalue().strip().splitlines()[-1])
        assert line["message"] == "hola" and line["request_id"] == "abc123"

    def test_text_output_is_readable(self):
        stream = io.StringIO()
        configure_logging("INFO", json_logs=False, stream=stream)
        logging.getLogger("app.x").warning("cuidado", extra={"request_id": "abc123"})
        assert "WARNING" in stream.getvalue() and "[abc123]" in stream.getvalue()
        assert "cuidado" in stream.getvalue()

    def test_calling_it_twice_does_not_duplicate_lines(self):
        stream = io.StringIO()
        configure_logging("INFO", True, stream)
        configure_logging("INFO", True, stream)
        logging.getLogger("app.x").info("once")
        assert stream.getvalue().count("once") == 1

    def test_handlers_it_did_not_create_are_left_alone(self):
        foreign = logging.NullHandler()
        logging.getLogger().addHandler(foreign)
        configure_logging("INFO", True, io.StringIO())
        assert foreign in logging.getLogger().handlers

    def test_level_applies_and_uvicorn_access_log_is_replaced(self):
        configure_logging("WARNING", True, io.StringIO())
        assert logging.getLogger().level == logging.WARNING
        assert logging.getLogger("uvicorn.access").disabled is True
        assert logging.getLogger("uvicorn.error").propagate is True

    def test_json_default_depends_on_the_environment(self):
        def cfg(**kw):
            return Settings(_env_file=None, database_url="x", secret_key="k" * 48, **kw)
        assert cfg(app_env="dev").use_json_logs is False
        assert cfg(app_env="prod").use_json_logs is True
        assert cfg(app_env="dev", log_format="json").use_json_logs is True
        assert cfg(app_env="prod", log_format="text").use_json_logs is False


# --- Request id ------------------------------------------------------------------------------

class TestRequestId:
    def test_every_response_gets_one(self, api):
        r = api().get("/health/live")
        assert len(r.headers["X-Request-ID"]) == 32

    def test_each_request_gets_a_different_one(self, api):
        c = api()
        assert c.get("/health/live").headers["X-Request-ID"] != c.get("/health/live").headers["X-Request-ID"]

    def test_a_harmless_incoming_id_is_kept(self, api):
        r = api().get("/health/live", headers={"X-Request-ID": "front-end.req_42"})
        assert r.headers["X-Request-ID"] == "front-end.req_42"

    @pytest.mark.parametrize("bad", ["x" * 65, "has space", "semi;colon", "quote\"", "<script>"])
    def test_a_suspicious_incoming_id_is_replaced(self, api, bad):
        r = api().get("/health/live", headers={"X-Request-ID": bad})
        assert r.headers["X-Request-ID"] != bad and len(r.headers["X-Request-ID"]) == 32

    def test_error_responses_carry_it_too(self, api):
        c = api(allowed_hosts="api.test")
        assert "X-Request-ID" in c.get("/no-such-route", headers={"Host": "api.test"}).headers
        assert "X-Request-ID" in c.get("/health/live", headers={"Host": "evil.test"}).headers  # host check 400
        assert "X-Request-ID" in c.get("/users").headers  # 401/403

    def test_the_id_is_readable_inside_an_endpoint(self, api):
        c = api()
        c.app.add_api_route("/whoami", lambda: {"request_id": get_request_id()})
        r = c.get("/whoami")
        assert r.json()["request_id"] == r.headers["X-Request-ID"]


# --- Access log --------------------------------------------------------------------------------

class TestAccessLog:
    def test_one_line_per_request_with_the_useful_fields(self, api, caplog):
        with caplog.at_level(logging.INFO, logger="app.access"):
            r = api().get("/catalogs/roles")
        (record,) = _access_records(caplog)
        assert (record.method, record.path, record.status) == ("GET", "/catalogs/roles", 200)
        assert record.request_id == r.headers["X-Request-ID"]
        assert record.route == "/catalogs/roles" and record.duration_ms >= 0
        assert record.levelno == logging.INFO

    def test_the_query_string_never_reaches_the_log(self, api, caplog):
        with caplog.at_level(logging.INFO, logger="app.access"):
            api().get("/catalogs/roles?email=persona@privado.com&token=secreto123")
        text = " ".join(f"{r.getMessage()} {vars(r)}" for r in _access_records(caplog))
        assert "persona@privado.com" not in text and "secreto123" not in text

    def test_credentials_and_bodies_never_reach_the_log(self, api, caplog, db):
        user = factories.make_user(db, "citizen")
        with caplog.at_level(logging.DEBUG):
            c = api()
            c.post("/auth/login", json={"email": user.email, "password": "ClaveSuperSecreta99"})
        everything = " ".join(f"{r.getMessage()} {vars(r)}" for r in caplog.records)
        assert "ClaveSuperSecreta99" not in everything

    def test_authenticated_requests_include_the_user_id(self, client_as, db, caplog):
        user = factories.make_user(db, "citizen")
        with caplog.at_level(logging.INFO, logger="app.access"):
            client_as(user).get(f"/users/{user.id}")
        assert _access_records(caplog)[-1].user_id == str(user.id)

    def test_anonymous_requests_have_no_user_id(self, api, caplog):
        with caplog.at_level(logging.INFO, logger="app.access"):
            api().get("/catalogs/roles")
        assert _access_records(caplog)[-1].user_id is None

    @pytest.mark.parametrize("path", ["/health/live", "/health/ready", "/health", "/metrics"])
    def test_probes_and_scrapes_are_not_logged(self, api, caplog, path):
        with caplog.at_level(logging.INFO, logger="app.access"):
            api(metrics_token=METRICS_TOKEN).get(path, headers={"Authorization": f"Bearer {METRICS_TOKEN}"})
        assert _access_records(caplog) == []

    def test_severity_follows_the_status(self, api, caplog):
        c = api()
        with caplog.at_level(logging.INFO, logger="app.access"):
            c.get("/catalogs/roles")          # 200
            c.get("/users")                   # 401/403
            c.get("/no-such-route")           # 404
        levels = [r.levelno for r in _access_records(caplog)]
        assert levels[0] == logging.INFO and levels[1] == logging.WARNING and levels[2] == logging.INFO


class TestUnhandledErrors:
    @staticmethod
    def _boom_app(api):
        c = api()

        def boom():
            raise RuntimeError("database password is hunter2")

        c.app.add_api_route("/boom", boom)
        return c

    def test_the_client_gets_a_generic_500_with_a_request_id(self, api):
        r = self._boom_app(api).get("/boom")
        assert r.status_code == 500
        body = r.json()
        assert body["detail"] == "Error interno del servidor"
        assert body["request_id"] == r.headers["X-Request-ID"]
        assert "hunter2" not in r.text  # internals never leak to the client

    def test_the_traceback_is_logged_with_the_same_id(self, api, caplog):
        with caplog.at_level(logging.ERROR):
            r = self._boom_app(api).get("/boom")
        errors = [x for x in caplog.records if x.getMessage().startswith("Unhandled exception")]
        assert errors and errors[0].request_id == r.headers["X-Request-ID"]
        assert errors[0].exc_info and "RuntimeError" in repr(errors[0].exc_info[1])

    def test_the_access_line_says_500_at_error_level(self, api, caplog):
        with caplog.at_level(logging.INFO, logger="app.access"):
            self._boom_app(api).get("/boom")
        record = _access_records(caplog)[-1]
        assert record.status == 500 and record.levelno == logging.ERROR


# --- Metrics --------------------------------------------------------------------------------------

class TestMetricsEndpoint:
    def _scrape(self, c, token=METRICS_TOKEN):
        return c.get("/metrics", headers={"Authorization": f"Bearer {token}"})

    def test_it_does_not_exist_until_a_token_is_configured(self, api):
        c = api(metrics_token=None)
        assert c.get("/metrics").status_code == 404
        assert self._scrape(c).status_code == 404

    @pytest.mark.parametrize("header", [None, "Bearer wrong", "wrong", "Basic abc", "Bearer "])
    def test_wrong_or_missing_credentials_are_rejected(self, api, header):
        c = api(metrics_token=METRICS_TOKEN)
        r = c.get("/metrics", headers={"Authorization": header} if header else {})
        assert r.status_code == 401 and r.headers["WWW-Authenticate"] == "Bearer"

    def test_the_right_token_returns_prometheus_text(self, api):
        r = self._scrape(api(metrics_token=METRICS_TOKEN))
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
        assert "http_requests_total" in r.text or "# HELP" in r.text

    def test_it_is_not_advertised_in_the_openapi_schema(self, api):
        schema = api(metrics_token=METRICS_TOKEN, app_env="dev").get("/openapi.json").json()
        assert "/metrics" not in schema["paths"]


class TestMetricValues:
    def test_requests_are_counted_by_route_template_not_by_real_path(self, api, client_as, db):
        user = factories.make_user(db, "citizen")
        template = {"method": "GET", "route": "/users/{user_id}", "status": "200"}
        before = _sample("http_requests_total", **template)
        client_as(user).get(f"/users/{user.id}")
        assert _sample("http_requests_total", **template) == before + 1

    def test_unknown_paths_collapse_into_one_series(self, api):
        c = api()
        series = {"method": "GET", "route": "unmatched", "status": "404"}
        before = _sample("http_requests_total", **series)
        for i in range(15):
            c.get(f"/random/{i}/{'x' * i}")
        assert _sample("http_requests_total", **series) == before + 15

    def test_durations_are_recorded(self, api):
        before = _sample("http_request_duration_seconds_count", method="GET", route="/catalogs/roles")
        api().get("/catalogs/roles")
        assert _sample("http_request_duration_seconds_count", method="GET", route="/catalogs/roles") == before + 1

    def test_the_metrics_endpoint_does_not_count_itself(self, api):
        c = api(metrics_token=METRICS_TOKEN)
        before = _sample("http_requests_total", method="GET", route="/metrics", status="200")
        c.get("/metrics", headers={"Authorization": f"Bearer {METRICS_TOKEN}"})
        assert _sample("http_requests_total", method="GET", route="/metrics", status="200") == before

    def test_security_relevant_statuses_are_counted(self, api):
        c = api()
        unauthorized = _sample("security_events_total", event="unauthorized")
        forbidden = _sample("security_events_total", event="forbidden")
        c.get("/users")  # no token
        assert (_sample("security_events_total", event="unauthorized")
                + _sample("security_events_total", event="forbidden")) == unauthorized + forbidden + 1

    def test_forbidden_is_counted_for_an_authenticated_user_without_permission(self, client_as, db):
        before = _sample("security_events_total", event="forbidden")
        client_as(factories.make_user(db, "citizen")).get("/users")
        assert _sample("security_events_total", event="forbidden") == before + 1

    def test_login_outcomes(self, api, db):
        user = factories.make_user(db, "citizen")
        inactive = factories.make_user(db, "citizen", is_active=False)
        c = api()
        c.app.dependency_overrides[get_db] = lambda: db  # share the test transaction
        ok, failed, blocked = (_sample("auth_logins_total", outcome=o) for o in ("success", "failed", "blocked"))
        c.post("/auth/login", json={"email": user.email, "password": DEFAULT_PASSWORD})
        c.post("/auth/login", json={"email": user.email, "password": "incorrecta"})
        c.post("/auth/login", json={"email": inactive.email, "password": DEFAULT_PASSWORD})
        assert _sample("auth_logins_total", outcome="success") == ok + 1
        assert _sample("auth_logins_total", outcome="failed") == failed + 1
        assert _sample("auth_logins_total", outcome="blocked") == blocked + 1


# --- Sentry -----------------------------------------------------------------------------------------

def _settings(**kwargs):
    return Settings(_env_file=None, database_url="postgresql://x", secret_key="k" * 48, **kwargs)


class TestSentry:
    def test_disabled_without_a_dsn(self, monkeypatch):
        monkeypatch.setattr(sentry_sdk, "init", lambda **kw: pytest.fail("must not start without a DSN"))
        assert init_sentry(_settings()) is False

    def test_enabled_with_privacy_preserving_options(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(sentry_module.sentry_sdk, "init", lambda **kw: captured.update(kw))
        started = init_sentry(_settings(
            sentry_dsn="https://key@example.invalid/1", app_env="prod",
            sentry_release="abc123", sentry_traces_sample_rate=0.1))
        assert started is True
        assert captured["dsn"] == "https://key@example.invalid/1"
        assert captured["environment"] == "prod" and captured["release"] == "abc123"
        assert captured["traces_sample_rate"] == 0.1
        assert captured["send_default_pii"] is False
        assert captured["max_request_body_size"] == "never"
        assert captured["before_send"] is scrub_event

    def test_traces_are_off_by_default(self):
        assert _settings().sentry_traces_sample_rate == 0.0


class TestScrubEvent:
    def test_removes_bodies_cookies_and_query_strings(self):
        event = {"request": {"data": {"password": "x"}, "cookies": {"s": "1"},
                             "query_string": "email=a@b.c", "url": "https://api/x"}}
        request = scrub_event(event)["request"]
        assert set(request) == {"url"}

    def test_masks_credential_headers_whatever_their_case(self):
        event = {"request": {"headers": {"Authorization": "Bearer abc", "COOKIE": "s=1",
                                         "X-Api-Key": "k", "Accept": "application/json"}}}
        headers = scrub_event(event)["request"]["headers"]
        assert headers["Authorization"] == headers["COOKIE"] == headers["X-Api-Key"] == FILTERED
        assert headers["Accept"] == "application/json"

    @pytest.mark.parametrize("event", [{}, {"request": None}, {"request": {}}, {"request": {"headers": None}}])
    def test_tolerates_events_without_a_request(self, event):
        assert scrub_event(event) == event

    def test_the_authenticated_user_is_reported_by_id_only(self, monkeypatch, client_as, db):
        seen = []
        monkeypatch.setattr("app.core.security.sentry_sdk.set_user", lambda user: seen.append(user))
        user = factories.make_user(db, "citizen")
        client_as(user).get(f"/users/{user.id}")
        assert seen and seen[-1] == {"id": str(user.id)}
