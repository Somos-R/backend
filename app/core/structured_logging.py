"""Structured logging: one JSON object per line in staging/prod, readable text in dev.

Every record carries the request id, so all the lines of one request (access log, errors,
audit events) can be found with a single search. Nothing here logs request bodies, query
strings or headers: they hold passwords, tokens and personal data.
"""
import json
import logging
import sys
from datetime import datetime, timezone

from app.core.context import get_request_id

# Attributes every LogRecord has; anything else was passed through `extra=` and is exported.
_STANDARD = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime", "taskName"}

_HANDLER_MARK = "_somosr_handler"


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = get_request_id()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD and not key.startswith("_") and value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


TEXT_FORMAT = "%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s: %(message)s"


def configure_logging(level: str = "INFO", json_logs: bool = False, stream=None) -> None:
    """Install (or replace) this app's root handler. Safe to call repeatedly.

    Handlers added by someone else (pytest's caplog, a debugger) are left alone.
    """
    root = logging.getLogger()
    for handler in [h for h in root.handlers if getattr(h, _HANDLER_MARK, False)]:
        root.removeHandler(handler)

    handler = logging.StreamHandler(stream or sys.stdout)
    setattr(handler, _HANDLER_MARK, True)
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(JsonFormatter() if json_logs else logging.Formatter(TEXT_FORMAT))
    root.addHandler(handler)
    root.setLevel(level.upper())

    # uvicorn brings its own handlers and an access log we replace with ours (app.access):
    # route its error log through the root handler so it is JSON too, and silence the duplicate.
    uvicorn_error = logging.getLogger("uvicorn.error")
    uvicorn_error.handlers = []
    uvicorn_error.propagate = True
    logging.getLogger("uvicorn.access").disabled = True
