"""Rate limiting (slowapi).

Two layers share the same storage and on/off switch:
- per endpoint, by IP: the strict limits on login, registration, token flows and /admin,
  declared with `@limiter.limit(...)` on each route;
- general, by account: `GeneralRateLimitMiddleware` puts every request under a read, write or
  CSV export budget, so no endpoint is left without a limit, including the ones added later.

Limits are read from settings at request time, so tests and operators can change
them without touching code. Storage is in-process by default; point
RATE_LIMIT_STORAGE_URI at Redis so several workers/instances share the counters.

Behind a proxy (Railway, etc.) run uvicorn with --proxy-headers and
--forwarded-allow-ips, otherwise every client shares the proxy's IP.
"""
import jwt
from fastapi import Request
from fastapi.responses import JSONResponse
from limits import parse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import settings
from app.core.security import BACKOFFICE, PORTAL, REQUIRED_CLAIMS

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
    return too_many_requests()


def too_many_requests() -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": "Demasiadas solicitudes. Intenta de nuevo en unos minutos", "code": "rate_limited"},
        headers={"Retry-After": "60"},
    )


# Probes, scrapes and the API docs are not client traffic.
GENERAL_LIMIT_EXEMPT_PATHS = ("/health", "/metrics", "/docs", "/redoc", "/openapi.json")
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def general_limit_for(method: str, path: str) -> tuple[str, str] | None:
    """The (budget, limit) a request counts against, or None if it is exempt.

    CSV exports are the heaviest queries and get their own, stricter budget.
    """
    if method == "OPTIONS" or path.startswith(GENERAL_LIMIT_EXEMPT_PATHS):
        return None
    if path.endswith(".csv"):
        return "exports", settings.rate_limit_exports
    if method in WRITE_METHODS:
        return "writes", settings.rate_limit_writes
    return "reads", settings.rate_limit_reads


def account_or_ip_key(request: Request) -> str:
    """The account of a validly signed access token, or else the client's IP.

    Keyed by account, staff that share an office IP do not exhaust each other's budget. Only the
    signature, expiry and audience are checked (no database): an unsigned or tampered token cannot
    pick its own bucket and falls back to the IP. Revocation is left to the authentication itself.
    """
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer" and token:
        try:
            payload = jwt.decode(
                token,
                settings.secret_key,
                algorithms=[settings.algorithm],
                audience=[PORTAL, BACKOFFICE],
                options={"require": REQUIRED_CLAIMS},
            )
            return f"user:{payload['sub']}"
        except jwt.InvalidTokenError:
            pass
    return f"ip:{get_remote_address(request)}"


class GeneralRateLimitMiddleware:
    """Counts every request against its account's (or IP's) read, write or export budget.

    Pure ASGI, so bodies are never buffered. It must sit inside CORS so that a browser can read
    the 429.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and limiter.enabled:
            budget = general_limit_for(scope["method"], scope["path"])
            if budget is not None:
                name, limit = budget
                key = account_or_ip_key(Request(scope))
                if not limiter.limiter.hit(parse(limit), "general", name, key):
                    await too_many_requests()(scope, receive, send)
                    return
        await self.app(scope, receive, send)
