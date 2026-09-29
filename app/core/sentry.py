"""Error tracking. Does nothing unless SENTRY_DSN is set.

Configured to send as little personal data as possible: no request bodies (they carry passwords
and tokens), no cookies, no query strings, credentials headers masked, and only the user *id*.
"""
import logging
from typing import Any

import sentry_sdk

from app.core.config import Settings

logger = logging.getLogger(__name__)

SENSITIVE_HEADERS = frozenset({"authorization", "cookie", "set-cookie", "x-api-key", "proxy-authorization"})
FILTERED = "[Filtered]"


def scrub_event(event: Any, hint: Any = None) -> Any:
    """before_send hook: strip whatever could identify or authenticate someone."""
    request = event.get("request")
    if isinstance(request, dict):
        for field in ("data", "cookies", "query_string"):
            request.pop(field, None)
        headers = request.get("headers")
        if isinstance(headers, dict):
            for name in list(headers):
                if name.lower() in SENSITIVE_HEADERS:
                    headers[name] = FILTERED
    return event


def init_sentry(config: Settings) -> bool:
    """Start Sentry if a DSN is configured. Returns whether it was started."""
    if not config.sentry_dsn:
        return False
    sentry_sdk.init(
        dsn=config.sentry_dsn,
        environment=config.app_env,
        release=config.sentry_release,
        traces_sample_rate=config.sentry_traces_sample_rate,
        send_default_pii=False,
        max_request_body_size="never",
        before_send=scrub_event,
    )
    logger.info("Sentry enabled", extra={"environment": config.app_env})
    return True
