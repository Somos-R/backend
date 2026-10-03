"""IP-based rate limiting (slowapi).

Limits are read from settings at request time, so tests and operators can change
them without touching code. Storage is in-process by default; point
RATE_LIMIT_STORAGE_URI at Redis so several workers/instances share the counters.

Behind a proxy (Railway, etc.) run uvicorn with --proxy-headers and
--forwarded-allow-ips, otherwise every client shares the proxy's IP.
"""
from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.core.config import settings

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=settings.rate_limit_storage_uri,
    enabled=settings.rate_limit_enabled,
)


def login_limit() -> str:
    return settings.rate_limit_login


def register_limit() -> str:
    return settings.rate_limit_register


def forgot_password_limit() -> str:
    return settings.rate_limit_forgot_password


def admin_auth_limit() -> str:
    return settings.rate_limit_admin_auth


def applications_limit() -> str:
    return settings.rate_limit_applications


def application_uploads_limit() -> str:
    return settings.rate_limit_application_uploads


def admin_limit() -> str:
    return settings.rate_limit_admin


def token_flow_limit() -> str:
    return settings.rate_limit_token_flows


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": "Demasiadas solicitudes. Intenta de nuevo en unos minutos", "code": "rate_limited"},
        headers={"Retry-After": "60"},
    )
