"""Prometheus metrics and the protected /metrics endpoint.

Labels are deliberately low-cardinality: the route *template* (`/users/{user_id}`), never the
real path, so an attacker cannot blow up the series count by requesting random URLs.

With several uvicorn workers each process has its own counters; set PROMETHEUS_MULTIPROC_DIR
(the production image does) so /metrics aggregates all of them.
"""
import hmac
import os

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    multiprocess,
)

from app.core.config import settings
from app.core.errors import ApiError

REQUESTS = Counter(
    "http_requests_total", "HTTP requests", ["method", "route", "status"])
DURATION = Histogram(
    "http_request_duration_seconds", "HTTP request duration in seconds", ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10))
IN_PROGRESS = Gauge(
    "http_requests_in_progress", "Requests being served right now", multiprocess_mode="livesum")
LOGINS = Counter(
    "auth_logins_total", "Login attempts by outcome", ["outcome"])
SECURITY_EVENTS = Counter(
    "security_events_total", "Security-relevant responses (401, 403, 429)", ["event"])

_SECURITY_STATUS = {401: "unauthorized", 403: "forbidden", 429: "rate_limited"}


def security_event_for(status_code: int) -> str | None:
    return _SECURITY_STATUS.get(status_code)


def _registry():
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        return registry
    return REGISTRY


def require_metrics_token(request: Request) -> None:
    expected = settings.metrics_token
    if not expected:
        # Not configured: pretend the endpoint does not exist rather than advertise it.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    header = request.headers.get("authorization", "")
    supplied = header[7:] if header.lower().startswith("bearer ") else ""
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise ApiError("unauthorized", 
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )


router = APIRouter(include_in_schema=False)


@router.get("/metrics", dependencies=[Depends(require_metrics_token)])
def metrics():
    return Response(generate_latest(_registry()), media_type=CONTENT_TYPE_LATEST)
