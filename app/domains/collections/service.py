import uuid
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone

from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.errors import ApiError
from app.core.pagination import paginate
from app.core.spatial import distance_meters, nearest_first, point, within_radius
from app.domains.audit import service as audit
from app.domains.audit.actions import Action
from app.domains.collections.models import Collection, CollectionStatus, RejectionReason
from app.domains.collections.schemas import (
    CancelCollectionRequest,
    CollectionResponse,
    CreateCollectionRequest,
    NearbyCollectionResponse,
    RejectCollectionRequest,
)
from app.domains.inventory.service import ensure_material_is_active
from app.domains.organizations.enums import OrganizationStatus, OrganizationType
from app.domains.organizations.models import Organization
from app.domains.organizations.scope import collection_scope
from app.domains.users.models import User

# Colombia has no daylight saving time: "today" for a requester is always UTC-5.
LOCAL_TIMEZONE = timezone(timedelta(hours=-5))


# Who generates the material asks for it to be picked up: a citizen or a building.
REQUESTER_TYPES = frozenset({"citizen", "building"})


def can_request(actor: User) -> bool:
    return actor.user_type_code in REQUESTER_TYPES


def _local_today() -> date:
    return datetime.now(LOCAL_TIMEZONE).date()


def _not_found() -> ApiError:
    return ApiError("collection_not_found", status_code=status.HTTP_404_NOT_FOUND,
                    detail="Solicitud de recolección no encontrada")


def _locked(db: Session, collection_id: uuid.UUID, *conditions) -> Collection:
    """The request under a row lock, so a concurrent change of state cannot slip in; 404 outside `conditions`."""
    collection = db.scalars(
        select(Collection)
        .where(Collection.id == collection_id, *conditions)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if collection is None:
        raise _not_found()
    return collection


def _own_collection(db: Session, actor: User, collection_id: uuid.UUID) -> Collection:
    """Only the account that asked reaches a request; anyone else gets 404, as if it did not exist."""
    collection = db.get(Collection, collection_id)
    if collection is None or collection.requester_id != actor.id:
        raise _not_found()
    return collection


def assign_association(db: Session, latitude: object, longitude: object) -> uuid.UUID | None:
    """The association that handles a request made at this point.

    The pilot has a single association, so it is the only approved one. This is the one place to replace with
    coverage by area (the point inside an association's polygon, the nearest when several cover it) when a second
    association joins. With none, or with more than one and no area to tell them apart, nobody is assigned: the
    request stays unassigned instead of landing at the wrong organization.
    """
    ids = db.scalars(
        select(Organization.id)
        .where(Organization.type == OrganizationType.association, Organization.status == OrganizationStatus.approved)
        .limit(2)).all()
    return ids[0] if len(ids) == 1 else None


def _ensure_under_daily_limit(db: Session, actor: User) -> None:
    """One account may not flood the associations' inbox: at most `collections_max_per_day` in the last 24 hours.

    Cancelled and rejected requests count too, otherwise asking and cancelling would be a way round the limit.
    A retry with the same `Idempotency-Key` never gets here (it returns the original), so it is not counted twice.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    created = db.scalar(select(func.count()).select_from(Collection).where(
        Collection.requester_id == actor.id, Collection.created_at >= since)) or 0
    if created >= settings.collections_max_per_day:
        raise ApiError("collections_daily_limit", status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                       detail="Alcanzaste el máximo de solicitudes de recolección por día. Intenta más tarde")


def create_collection(
    db: Session, actor: User, request: CreateCollectionRequest, client_created_at: datetime | None = None,
) -> Collection:
    _ensure_under_daily_limit(db, actor)
    if request.scheduled_date is not None and request.scheduled_date < _local_today():
        raise ApiError("scheduled_date_in_past", status_code=422,
                       detail="La fecha programada no puede ser anterior a hoy")
    for code in request.materials:
        ensure_material_is_active(db, code)

    collection = Collection(
        requester_id=actor.id,
        status=CollectionStatus.pending_assignment,
        association_id=assign_association(db, request.latitude, request.longitude),
        address=request.address,
        latitude=request.latitude,
        longitude=request.longitude,
        location=point(request.latitude, request.longitude),
        materials=request.materials,
        notes=request.notes,
        scheduled_date=request.scheduled_date,
        client_created_at=client_created_at,
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
    query = (select(Collection).where(Collection.requester_id == actor.id)
             .options(selectinload(Collection.rejection_reason)))
    if status_ is not None:
        query = query.where(Collection.status == status_)
    return paginate(db, query, Collection.created_at.desc(), Collection.id.desc(), limit=limit, offset=offset)


def list_association_inbox(
    db: Session, actor: User, association_id: uuid.UUID, *, status_: CollectionStatus | None, limit: int, offset: int,
) -> tuple[int, list[Collection]]:
    """The requests assigned to the actor's own association, oldest first: the one waiting longest is reviewed
    first. Any other association's id answers 404, as if it did not exist."""
    if association_id != actor.organization_id:
        raise ApiError("organization_not_found", status_code=status.HTTP_404_NOT_FOUND,
                       detail="Organización no encontrada")
    query = (select(Collection).where(collection_scope(actor))
             .options(selectinload(Collection.rejection_reason)))
    if status_ is not None:
        query = query.where(Collection.status == status_)
    return paginate(db, query, Collection.created_at.asc(), Collection.id.asc(), limit=limit, offset=offset)


def nearby_collections(
    db: Session, actor: User, *, latitude: float, longitude: float, radius_meters: float,
    status_: CollectionStatus, limit: int, offset: int,
) -> tuple[int, list[NearbyCollectionResponse]]:
    """The requests of the actor's own association inside `radius_meters` of a point, nearest first.

    The scope (only what is assigned to the actor's association) comes first and is never widened by the radius.
    """
    center = point(latitude, longitude)
    conditions = (collection_scope(actor), Collection.status == status_,
                  within_radius(Collection.location, center, radius_meters))
    total = db.scalar(select(func.count()).select_from(select(Collection.id).where(*conditions).subquery())) or 0
    rows = db.execute(
        select(Collection, distance_meters(Collection.location, center))
        .where(*conditions)
        .options(selectinload(Collection.rejection_reason))
        # `id` after the distance: pages never overlap when two requests are the same distance away.
        .order_by(nearest_first(Collection.location, center), Collection.id)
        .offset(offset).limit(limit)).all()
    return total, [
        NearbyCollectionResponse.model_validate(
            {**CollectionResponse.model_validate(collection).model_dump(), "distance_meters": meters})
        for collection, meters in rows]


def get_collection(db: Session, actor: User, collection_id: uuid.UUID) -> Collection:
    return _own_collection(db, actor, collection_id)


def cancel_collection(
    db: Session, actor: User, collection_id: uuid.UUID, request: CancelCollectionRequest,
) -> Collection:
    """Possible only while nobody has been assigned."""
    collection = _locked(db, collection_id, Collection.requester_id == actor.id)
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


def list_rejection_reasons(db: Session) -> Sequence[RejectionReason]:
    """The active reasons, the ones the requester can correct first."""
    return db.scalars(
        select(RejectionReason)
        .where(RejectionReason.is_active.is_(True))
        .order_by(RejectionReason.is_correctable.desc(), RejectionReason.code)
    ).all()


def _active_reason(db: Session, code: str) -> RejectionReason:
    reason = db.get(RejectionReason, code)
    if reason is None or not reason.is_active:
        raise ApiError("rejection_reason_not_found", status_code=422,
                       detail=f"'{code}' no es un motivo de rechazo válido")
    return reason


def reject_collection(
    db: Session, actor: User, collection_id: uuid.UUID, request: RejectCollectionRequest,
) -> Collection:
    """An association turns down a request assigned to it that no route has taken yet."""
    collection = _locked(db, collection_id, collection_scope(actor))
    reason = _active_reason(db, request.reason_code)
    if collection.status != CollectionStatus.pending_assignment:
        raise ApiError("collection_not_rejectable", status_code=status.HTTP_409_CONFLICT,
                       detail=f"Solo se puede rechazar una solicitud pendiente de asignación. "
                              f"Estado actual: {collection.status.value}")
    collection.status = CollectionStatus.rejected
    collection.rejection_reason = reason
    collection.rejected_by = actor.id
    collection.rejected_at = datetime.now(timezone.utc)
    audit.record(db, Action.COLLECTION_REJECTED, actor=actor, target_type="collection", target_id=collection.id,
                 details={"reason_code": reason.code})
    db.commit()
    db.refresh(collection)
    return collection
