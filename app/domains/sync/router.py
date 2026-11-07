from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.rate_limit import limiter, register_limit
from app.core.security import get_current_user
from app.domains.sync import service
from app.domains.sync.docs import SYNC_BATCH_DOCS
from app.domains.sync.schemas import SyncBatchRequest, SyncBatchResponse
from app.domains.users.models import User

router = APIRouter(prefix="/sync", tags=["sync"])


@router.post("/batch", response_model=SyncBatchResponse, **SYNC_BATCH_DOCS)
@limiter.limit(register_limit)
def sync_batch(
    request: Request,
    body: SyncBatchRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    return service.process_batch(db, actor, body)
