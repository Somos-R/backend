import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.core.pagination import paginate
from app.core.spatial import point
from app.domains.audit import service as audit
from app.domains.audit.actions import Action
from app.domains.collections.models import Collection, CollectionStatus
from app.domains.collections.schemas import (
    CancelCollectionRequest,
    CreateCollectionRequest,
)
from app.domains.inventory.service import ensure_material_is_active
from app.domains.users.models import User

# Colombia has no daylight saving time: "today" for a requester is always UTC-5.
LOCAL_TIMEZONE = timezone(timedelta(hours=-5))


def _local_today() -> date:
    return datetime.now(LOCAL_TIMEZONE).date()


def _not_found() -> ApiError:
    return ApiError("collection_not_found", status_code=status.HTTP_404_NOT_FOUND,
                    detail="Solicitud de recolección no encontrada")


def _own_collection(db: Session, actor: User, collection_id: uuid.UUID) -> Collection:
    """Only the account that asked reaches a request; anyone else gets 404, as if it did not exist."""
    collection = db.get(Collection, collection_id)
    if collection is None or collection.requester_id != actor.id:
        raise _not_found()
    return collection


def create_collection(db: Session, actor: User, request: CreateCollectionRequest) -> Collection:
    if request.scheduled_date is not None and request.scheduled_date < _local_today():
        raise ApiError("scheduled_date_in_past", status_code=422,
                       detail="La fecha programada no puede ser anterior a hoy")
    for code in request.materials:
        ensure_material_is_active(db, code)

    collection = Collection(
        requester_id=actor.id,
        status=CollectionStatus.pending_assignment,
        address=request.address,
        latitude=request.latitude,
        longitude=request.longitude,
        location=point(request.latitude, request.longitude),
        materials=request.materials,
        notes=request.notes,
        scheduled_date=request.scheduled_date,
    )
    db.add(collection)
    db.flush()
    # Material codes only: the address and the notes are personal data and stay out of the trail.
    audit.record(db, Action.COLLECTION_REQUESTED, actor=actor, target_type="collection", target_id=collection.id,
                 details={"materials": request.materials})
    db.commit()
    db.refresh(collection)
    return collection


def list_collections(
    db: Session, actor: User, *, status_: CollectionStatus | None, limit: int, offset: int,
) -> tuple[int, list[Collection]]:
    query = select(Collection).where(Collection.requester_id == actor.id)
    if status_ is not None:
        query = query.where(Collection.status == status_)
    return paginate(db, query, Collection.created_at.desc(), Collection.id.desc(), limit=limit, offset=offset)


def get_collection(db: Session, actor: User, collection_id: uuid.UUID) -> Collection:
    return _own_collection(db, actor, collection_id)


def cancel_collection(
    db: Session, actor: User, collection_id: uuid.UUID, request: CancelCollectionRequest,
) -> Collection:
    """Possible only while nobody has been assigned; a row lock keeps a concurrent assignment from slipping in."""
    collection = db.scalars(
        select(Collection)
        .where(Collection.id == collection_id, Collection.requester_id == actor.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if collection is None:
        raise _not_found()
    if collection.status != CollectionStatus.pending_assignment:
        raise ApiError("collection_not_cancellable", status_code=status.HTTP_409_CONFLICT,
                       detail=f"Solo se puede cancelar una solicitud pendiente de asignación. "
                              f"Estado actual: {collection.status.value}")
    collection.status = CollectionStatus.cancelled
    collection.cancellation_reason = request.reason
    collection.cancelled_at = datetime.now(timezone.utc)
    audit.record(db, Action.COLLECTION_CANCELLED, actor=actor, target_type="collection", target_id=collection.id,
                 details={"with_reason": request.reason is not None})
    db.commit()
    db.refresh(collection)
    return collection
