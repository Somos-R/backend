import enum
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.csv_export import csv_response
from app.core.database import get_db
from app.core.idempotency import IdempotencyKeyHeader, create_once
from app.core.permissions import (
    PAYMENTS,
    WEIGHINGS_CREATE,
    WEIGHINGS_READ,
    WEIGHINGS_REVIEW,
    ensure_role,
    forbidden,
    has_role,
)
from app.core.security import get_current_user, require_roles
from app.core.sorting import SortOrder
from app.domains.users.models import User
from app.domains.weighings import integrity
from app.domains.weighings import service as weighing_service
from app.domains.weighings.docs import CREATE_WEIGHING_DOCS, VERIFY_WEIGHING_DOCS
from app.domains.weighings.models import AffiliationStatus, WeighingStatus
from app.domains.weighings.schemas import (
    CreateWeighingRequest,
    UpdateWeighingStatusRequest,
    WeighingListResponse,
    WeighingResponse,
    WeighingStatsResponse,
    WeighingVerificationResponse,
)

router = APIRouter(prefix="/weighings", tags=["weighings"])


class WeighingSort(str, enum.Enum):
    occurred_at = "occurred_at"
    kg = "kg"
    price_per_kg = "price_per_kg"
    total_value = "total_value"
    status = "status"



def get_weighing_reader(user: User = Depends(get_current_user)) -> User:
    if user.user_type_code == "recycler" or has_role(user, WEIGHINGS_READ):
        return user
    raise forbidden()


@router.get("", response_model=WeighingListResponse)
def list_weighings(
    recycler_id:   uuid.UUID | None = Query(default=None),
    material_code: str | None       = Query(default=None),
    warehouse_id:  uuid.UUID | None = Query(default=None),
    status_:       WeighingStatus | None = Query(default=None, alias="status"),
    affiliation:   AffiliationStatus | None = Query(default=None),
    q:             str | None       = Query(default=None, max_length=100),
    date_from:     datetime | None  = Query(default=None, description="occurred_at >= this instant"),
    date_to:       datetime | None  = Query(default=None, description="occurred_at <= this instant"),
    sort:          WeighingSort     = Query(default=WeighingSort.occurred_at),
    order:         SortOrder        = Query(default=SortOrder.desc),
    limit:         int              = Query(default=20, ge=1, le=100),
    offset:        int              = Query(default=0, ge=0),
    db:            Session          = Depends(get_db),
    actor:         User             = Depends(get_weighing_reader),
):
    total, weighings = weighing_service.list_weighings(
        db, actor, recycler_id=recycler_id, material_code=material_code, warehouse_id=warehouse_id,
        status_=status_, affiliation=affiliation, q=q, date_from=date_from, date_to=date_to,
        sort=sort.value, descending=order is SortOrder.desc, limit=limit, offset=offset)
    return WeighingListResponse(total=total, items=weighings)


@router.get("/export.csv", summary="Download the weighings matching the filters as CSV")
def export_weighings(
    recycler_id:   uuid.UUID | None = Query(default=None),
    material_code: str | None       = Query(default=None),
    warehouse_id:  uuid.UUID | None = Query(default=None),
    status_:       WeighingStatus | None = Query(default=None, alias="status"),
    affiliation:   AffiliationStatus | None = Query(default=None),
    q:             str | None       = Query(default=None, max_length=100),
    date_from:     datetime | None  = Query(default=None),
    date_to:       datetime | None  = Query(default=None),
    sort:          WeighingSort     = Query(default=WeighingSort.occurred_at),
    order:         SortOrder        = Query(default=SortOrder.desc),
    db:            Session          = Depends(get_db),
    actor:         User             = Depends(require_roles(*WEIGHINGS_READ)),
):
    weighings = weighing_service.export_weighings(
        db, actor, recycler_id=recycler_id, material_code=material_code, warehouse_id=warehouse_id,
        status_=status_, affiliation=affiliation, q=q, date_from=date_from, date_to=date_to,
        sort=sort.value, descending=order is SortOrder.desc)
    header = ["date", "delivered_by", "document_type", "document", "affiliation", "material", "warehouse",
              "kg", "price_per_kg", "total_value", "status"]
    rows = [
        [w.occurred_at.isoformat(),
         w.recycler.full_name if w.recycler else w.seller_name,
         w.recycler.id_type if w.recycler else w.seller_id_type,
         w.recycler.id_number if w.recycler else w.seller_id_number,
         w.affiliation_status.value, w.material.label, w.warehouse.name,
         w.kg, w.price_per_kg, w.total_value, w.status.value]
        for w in weighings]
    return csv_response("weighings.csv", header, rows)


@router.get("/stats", response_model=WeighingStatsResponse)
def weighing_stats(
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*WEIGHINGS_READ)),
):
    count_month, total_kg, pending, by_material = weighing_service.month_stats(db, actor)
    return WeighingStatsResponse(
        total_weighings_month=count_month,
        total_kg_month=total_kg,
        pending_count=pending,
        by_material=[{"material": material, "kg": float(kg)} for material, kg in by_material],
    )


@router.post("", response_model=WeighingResponse, status_code=status.HTTP_201_CREATED, **CREATE_WEIGHING_DOCS)
def create_weighing(
    request:         CreateWeighingRequest,
    idempotency_key: IdempotencyKeyHeader = None,
    db:              Session = Depends(get_db),
    actor:           User    = Depends(require_roles(*WEIGHINGS_CREATE)),
):
    return create_once(
        db, user_id=actor.id, key=idempotency_key, operation="weighings.create", request=request,
        response_model=WeighingResponse, status_code=status.HTTP_201_CREATED,
        create=lambda: weighing_service.create_weighing(db, actor, request))


@router.get("/{weighing_id}", response_model=WeighingResponse)
def get_weighing(
    weighing_id: uuid.UUID,
    db:          Session = Depends(get_db),
    actor:       User    = Depends(get_weighing_reader),
):
    return weighing_service.get_weighing(db, actor, weighing_id)


@router.get("/{weighing_id}/verify", response_model=WeighingVerificationResponse, **VERIFY_WEIGHING_DOCS)
def verify_weighing(
    weighing_id: uuid.UUID,
    db:          Session = Depends(get_db),
    actor:       User    = Depends(get_weighing_reader),
):
    weighing, valid = weighing_service.verify_weighing(db, actor, weighing_id)
    return WeighingVerificationResponse(
        id=weighing.id, algorithm=integrity.ALGORITHM, record_hash=weighing.record_hash, valid=valid)


@router.patch("/{weighing_id}/status", response_model=WeighingResponse)
def update_weighing_status(
    weighing_id: uuid.UUID,
    request:     UpdateWeighingStatusRequest,
    db:          Session = Depends(get_db),
    current_user: User   = Depends(get_current_user),
):
    # Paying moves money; validating/rejecting is the review step.
    ensure_role(current_user, PAYMENTS if request.status == WeighingStatus.paid else WEIGHINGS_REVIEW)
    return weighing_service.update_status(db, current_user, weighing_id, request)
