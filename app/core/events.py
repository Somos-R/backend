"""Events between domains, in process.

A domain announces that something happened (`publish`) without importing whoever reacts; the reacting
domains register a handler (`subscribe`). Handlers run synchronously, in the order they were registered, on
the caller's database session: they take part in the same transaction, so an error in any of them undoes the
whole operation (the weighing is not validated if its purchase cannot be created). Nobody commits inside a
handler.

An event nobody listens to is a wiring mistake that would silently lose work (stock never added), so
publishing it fails loudly. The wiring lives in `app/domains/handlers.py`.

This is the seam for a message broker later: only `publish` and `subscribe` would change.
"""
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

Handler = Callable[[Session, Any], None]

_handlers: dict[type, list[Handler]] = defaultdict(list)


def subscribe(event_type: type, handler: Handler) -> None:
    if handler not in _handlers[event_type]:  # registering twice must not run it twice
        _handlers[event_type].append(handler)


def publish(db: Session, event: Any) -> None:
    handlers = _handlers.get(type(event))
    if not handlers:
        raise RuntimeError(f"Nobody handles {type(event).__name__}: handlers are not registered")
    for handler in handlers:
        handler(db, event)
