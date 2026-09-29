"""Outermost middleware: request id, access log and HTTP metrics.

Being outermost it sees every response, including the 400 of the host check and CORS
preflights. Pure ASGI (no BaseHTTPMiddleware) so bodies are never buffered.
"""
import logging
import re
import time
import uuid

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core import metrics
from app.core.context import client_ip_var, request_id_var

logger = logging.getLogger("app.access")

REQUEST_ID_HEADER = "X-Request-ID"
# Accept an id from an upstream proxy/caller only if it is short and harmless; it ends up in logs.
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
# Probes and scrapes every few seconds would drown the real traffic in the logs.
QUIET_PATHS = ("/health", "/metrics")


def _incoming_request_id(scope: Scope) -> str | None:
    for name, value in scope["headers"]:
        if name == b"x-request-id":
            candidate = value.decode("latin-1")
            return candidate if _VALID_REQUEST_ID.match(candidate) else None
    return None


def _route_template(scope: Scope) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _incoming_request_id(scope) or uuid.uuid4().hex
        request_id_var.set(request_id)
        client = scope.get("client")
        client_ip_var.set(client[0] if client else None)

        method, path = scope["method"], scope["path"]
        started = time.perf_counter()
        status_code = 500  # what a crash before any response amounts to
        metrics.IN_PROGRESS.inc()

        async def send_with_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            metrics.IN_PROGRESS.dec()
            duration = time.perf_counter() - started
            if path != "/metrics":
                route = _route_template(scope)
                metrics.REQUESTS.labels(method, route, str(status_code)).inc()
                metrics.DURATION.labels(method, route).observe(duration)
                event = metrics.security_event_for(status_code)
                if event:
                    metrics.SECURITY_EVENTS.labels(event).inc()
            if not path.startswith(QUIET_PATHS):
                self._log(scope, method, path, status_code, duration, request_id)

    @staticmethod
    def _log(scope: Scope, method: str, path: str, status_code: int, duration: float, request_id: str) -> None:
        level = logging.ERROR if status_code >= 500 else logging.WARNING if status_code in (401, 403, 429) else logging.INFO
        client = scope.get("client")
        logger.log(
            level,
            "%s %s -> %s",
            method, path, status_code,
            extra={
                "request_id": request_id,
                "method": method,
                "path": path,  # never the query string: it can hold emails or tokens
                "route": _route_template(scope),
                "status": status_code,
                "duration_ms": round(duration * 1000, 1),
                "client_ip": client[0] if client else None,
                "user_id": scope.get("state", {}).get("user_id"),
            },
        )
