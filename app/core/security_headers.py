"""Security response headers, added to every response.

Pure ASGI middleware: no per-request task, and streaming bodies are untouched.
"""
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Swagger UI / ReDoc load scripts and styles from a CDN; a strict CSP would break them.
DOC_PATHS = ("/docs", "/redoc", "/openapi.json")

BASE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-site",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=()",
}
# This is a JSON API: nothing in a response should ever be rendered, framed or executed.
API_CSP = "default-src 'none'; frame-ancestors 'none'"


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, hsts_max_age: int | None = None) -> None:
        self.app = app
        self.hsts = f"max-age={hsts_max_age}; includeSubDomains" if hsts_max_age else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope["path"]

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in BASE_HEADERS.items():
                    headers.setdefault(name, value)
                if not path.startswith(DOC_PATHS):
                    headers.setdefault("Content-Security-Policy", API_CSP)
                if path.startswith("/auth"):
                    # Responses carry tokens: never let a browser or proxy cache them.
                    headers["Cache-Control"] = "no-store"
                if self.hsts:
                    headers.setdefault("Strict-Transport-Security", self.hsts)
            await send(message)

        await self.app(scope, receive, send_with_headers)
