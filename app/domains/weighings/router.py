import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
from app.core.errors import ApiError
from app.core.pagination import paginate
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
from app.domains.audit import service as audit
from app.domains.audit.actions import Action
from app.domains.catalogs.models import DocumentType
from app.domains.inventory.models import Material
from app.domains.organizations import scope
from app.domains.users.models import User
from app.domains.weighings import service as weighing_service
from app.domains.weighings.models import AffiliationStatus, Weighing, WeighingStatus
from app.domains.weighings.schemas import (
    CreateWeighingRequest,
    UpdateWeighingStatusRequest,
    WeighingListResponse,
    WeighingResponse,
    WeighingStatsResponse,
)

router = APIRouter(prefix="/weighings", tags=["weighings"])


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
    limit:         int              = Query(default=20, ge=1, le=100),
    offset:        int              = Query(default=0, ge=0),
    db:            Session          = Depends(get_db),
    actor:         User             = Depends(get_weighing_reader),
):
    if actor.user_type_code == "recycler":
        if recycler_id and recycler_id != actor.id:
            raise forbidden()
        recycler_id = actor.id

    query = select(Weighing)
    if actor.user_type_code != "recycler":
        query = query.where(scope.weighing_scope(actor))

    if recycler_id:
        query = query.where(Weighing.recycler_id == recycler_id)
    if material_code:
        query = query.where(Weighing.material_code == material_code)
    if warehouse_id:
        query = query.where(Weighing.warehouse_id == warehouse_id)
    if status_:
        query = query.where(Weighing.status == status_)
    if affiliation:
        query = query.where(Weighing.affiliation_status == affiliation)

    total, weighings = paginate(
        db, query, Weighing.occurred_at.desc(), Weighing.id, limit=limit, offset=offset,
        options=(
            selectinload(Weighing.recycler),
            selectinload(Weighing.material),
            selectinload(Weighing.warehouse),
        ),
    )
    return WeighingListResponse(total=total, items=weighings)


@router.get("/stats", response_model=WeighingStatsResponse)
def weighing_stats(
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*WEIGHINGS_READ)),
):
    now   = datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    this_month = Weighing.occurred_at >= start
    mine = scope.weighing_scope(actor)

    count_month, total_kg = db.execute(
        select(func.count(Weighing.id), func.coalesce(func.sum(Weighing.kg), 0)).where(this_month, mine)
    ).one()
    pending = db.scalar(
        select(func.count(Weighing.id)).where(Weighing.status == WeighingStatus.pending_validation, mine)
    )
    by_material = db.execute(
        select(Weighing.material_code, func.sum(Weighing.kg))
        .where(this_month, mine)
        .group_by(Weighing.material_code)
        .order_by(Weighing.material_code)
    ).all()

    return WeighingStatsResponse(
        total_weighings_month=count_month,
        total_kg_month=total_kg,
        pending_count=pending,
        by_material=[{"material": material, "kg": float(kg)} for material, kg in by_material],
    )


@router.post("", response_model=WeighingResponse, status_code=status.HTTP_201_CREATED)
def create_weighing(
    request: CreateWeighingRequest,
    db:      Session = Depends(get_db),
    actor:   User    = Depends(require_roles(*WEIGHINGS_CREATE)),
):
    recycler = None
    if request.recycler_id is not None:
        recycler = db.get(User, request.recycler_id)
        if not recycler or recycler.user_type_code != "recycler":
            raise ApiError("recycler_not_found", status_code=status.HTTP_404_NOT_FOUND, detail="Reciclador no encontrado")
        weighing_service.ensure_recycler_is_active(recycler)
    elif request.seller is not None and db.get(DocumentType, request.seller.id_type) is None:
        raise ApiError("invalid_id_type", status_code=422, detail=f"id_type '{request.seller.id_type}' no es válido")

    if not db.get(Material, request.material_code):
        raise ApiError("material_not_found", status_code=status.HTTP_404_NOT_FOUND, detail="Material no encontrado")

    # Only in a warehouse of the actor's own ECA. The ECA receives the material whoever brings it; how the
    # seller relates to the ECA (linked, another association's, independent) is recorded, and decides
    # whether the weighing reaches an association.
    warehouse = scope.get_own_warehouse(db, actor, request.warehouse_id)
    affiliation = (
        scope.affiliation_of(db, warehouse.organization_id, recycler) if recycler is not None
        else AffiliationStatus.independent)
    seller = request.seller

    weighing = Weighing(
        id=uuid.uuid4(),
        recycler_id=request.recycler_id,
        seller_name=seller.full_name.strip() if seller else None,
        seller_id_type=seller.id_type if seller else None,
        seller_id_number=seller.id_number if seller else None,
        affiliation_status=affiliation,
        material_code=request.material_code,
        warehouse_id=request.warehouse_id,
        kg=request.kg,
        price_per_kg=request.price_per_kg,
    )
    db.add(weighing)
    audit.record(
        db, Action.WEIGHING_CREATED, actor=actor, target_type="weighing", target_id=weighing.id,
        details={"recycler_id": str(request.recycler_id) if request.recycler_id else None,
                 "walk_in": seller is not None, "affiliation": affiliation.value,
                 "material": request.material_code, "kg": str(request.kg), "price_per_kg": str(request.price_per_kg)})
    db.commit()
    db.refresh(weighing)
    return weighing


@router.get("/{weighing_id}", response_model=WeighingResponse)
def get_weighing(
    weighing_id: uuid.UUID,
    db:          Session = Depends(get_db),
    actor:       User    = Depends(get_weighing_reader),
):
    weighing = db.get(Weighing, weighing_id)
    # Someone else's weighing gets the same answer as a missing one: a recycler's peers, another
    # ECA, another association.
    outside = (
        weighing is not None
        and (weighing.recycler_id != actor.id if actor.user_type_code == "recycler"
             else db.scalar(select(Weighing.id).where(Weighing.id == weighing_id, scope.weighing_scope(actor))) is None)
    )
    if not weighing or outside:
        raise ApiError("weighing_not_found", status_code=status.HTTP_404_NOT_FOUND, detail="Pesaje no encontrado")
    return weighing


@router.patch("/{weighing_id}/status", response_model=WeighingResponse)
def update_weighing_status(
    weighing_id: uuid.UUID,
    request:     UpdateWeighingStatusRequest,
    db:          Session = Depends(get_db),
    current_user: User   = Depends(get_current_user),
):
    # Paying moves money; validating/rejecting is the review step.
    ensure_role(current_user, PAYMENTS if request.status == WeighingStatus.paid else WEIGHINGS_REVIEW)

    # FOR UPDATE: a concurrent transition on the same weighing waits here and then sees the new state.
    weighing = db.scalars(
        select(Weighing).where(Weighing.id == weighing_id, scope.weighing_scope(current_user)).with_for_update()
    ).first()
    if not weighing:
        raise ApiError("weighing_not_found", status_code=status.HTTP_404_NOT_FOUND, detail="Pesaje no encontrado")

    previous = weighing.status.value

    if request.status == WeighingStatus.validated:
        weighing_service.validate_weighing(db, weighing, current_user.id)
    elif request.status == WeighingStatus.rejected:
        if not request.rejection_reason:
            raise ApiError("rejection_reason_required", 
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="rejection_reason es requerido al rechazar",
            )
        weighing_service.reject_weighing(db, weighing, request.rejection_reason)
    elif request.status == WeighingStatus.paid:
        weighing_service.mark_paid(db, weighing)
    else:
        raise ApiError("invalid_transition", status_code=status.HTTP_400_BAD_REQUEST, detail="Transición de status no válida")

    audit.record(
        db,
        {WeighingStatus.validated: Action.WEIGHING_VALIDATED, WeighingStatus.rejected: Action.WEIGHING_REJECTED,
         WeighingStatus.paid: Action.WEIGHING_PAID}[request.status],
        actor=current_user, target_type="weighing", target_id=weighing.id,
        details={"from": previous, "material": weighing.material_code, "kg": str(weighing.kg),
                 "price_per_kg": str(weighing.price_per_kg),
                 "recycler_id": str(weighing.recycler_id) if weighing.recycler_id else None})
    db.commit()
    db.refresh(weighing)
    return weighing
