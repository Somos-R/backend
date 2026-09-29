"""The Resend backend, with the network mocked out."""
import logging

import httpx
import pytest

from app.core import email as email_module
from app.core.config import settings


@pytest.fixture
def resend(monkeypatch):
    monkeypatch.setattr(settings, "email_backend", "resend")
    monkeypatch.setattr(settings, "email_api_key", "re_test_key")
    monkeypatch.setattr(settings, "email_from", "Somos R <no-reply@somosr.test>")


def test_resend_receives_the_expected_request(resend, monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, json={"id": "abc"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    email_module.send_email("ana@test.com", "Asunto", "Cuerpo")

    (url, kwargs), = calls
    assert url == "https://api.resend.com/emails"
    assert kwargs["headers"]["Authorization"] == "Bearer re_test_key"
    assert kwargs["json"] == {
        "from": "Somos R <no-reply@somosr.test>", "to": ["ana@test.com"],
        "subject": "Asunto", "text": "Cuerpo",
    }
    assert kwargs["timeout"] == 10


def test_provider_failure_never_raises_into_the_request(resend, monkeypatch, caplog):
    def boom(url, **kwargs):
        raise httpx.ConnectError("provider down")

    monkeypatch.setattr(httpx, "post", boom)
    with caplog.at_level(logging.ERROR):
        email_module.send_email("ana@test.com", "Asunto", "Cuerpo")
    assert "Failed to send email" in caplog.text


def test_http_error_status_is_swallowed_and_logged(resend, monkeypatch, caplog):
    def rejected(url, **kwargs):
        return httpx.Response(403, json={}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", rejected)
    with caplog.at_level(logging.ERROR):
        email_module.send_email("ana@test.com", "Asunto", "Cuerpo")
    assert "Failed to send email" in caplog.text


def test_missing_api_key_drops_the_email_without_calling_out(resend, monkeypatch, caplog):
    monkeypatch.setattr(settings, "email_api_key", None)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("must not call the network"))
    with caplog.at_level(logging.ERROR):
        email_module.send_email("ana@test.com", "Asunto", "Cuerpo")
    assert "EMAIL_API_KEY is not set" in caplog.text


def test_console_backend_logs_the_message(monkeypatch, caplog):
    monkeypatch.setattr(settings, "email_backend", "console")
    with caplog.at_level(logging.WARNING):
        email_module.send_email("ana@test.com", "Asunto", "enlace-de-prueba")
    assert "enlace-de-prueba" in caplog.text
