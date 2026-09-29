"""Liveness and readiness probes.

- /health/live  : the process is up. Never touches dependencies; use it for restarts.
- /health/ready : the process can serve traffic, i.e. the database answers. Use it for routing.
- /health       : kept as an alias of /health/live for existing monitors.

None of them reveal versions or error details.
"""
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
@router.get("/health/live")
def live():
    return {"status": "ok"}


@router.get("/health/ready")
def ready(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        logger.exception("Readiness check failed: database unreachable")
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    return {"status": "ok"}
