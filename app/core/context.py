"""Per-request context, readable from anywhere (logs, audit trail) without passing `request` around.

Set by RequestContextMiddleware. ContextVars are copied into the worker threads that run
sync endpoints, so a value set before the endpoint is visible inside it.
"""
from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
client_ip_var: ContextVar[str | None] = ContextVar("client_ip", default=None)


def get_request_id() -> str | None:
    return request_id_var.get()


def get_client_ip() -> str | None:
    return client_ip_var.get()
