import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
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
from app.domains.inventory.models import Material, Warehouse
from app.domains.users.models import User
from app.domains.weighings import service as weighing_service
from app.domains.weighings.models import Weighing, WeighingStatus
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
    estado:        str | None       = Query(default=None),
    limit:         int              = Query(default=20, ge=1, le=100),
    offset:        int              = Query(default=0, ge=0),
    db:            Session          = Depends(get_db),
    actor:         User             = Depends(get_weighing_reader),
):
    if actor.user_type_code == "recycler":
        if recycler_id and recycler_id != actor.id:
            raise forbidden()
        recycler_id = actor.id

    query = db.query(Weighing)

    if recycler_id:
        query = query.filter(Weighing.recycler_id == recycler_id)
    if material_code:
        query = query.filter(Weighing.material_code == material_code)
    if warehouse_id:
        query = query.filter(Weighing.warehouse_id == warehouse_id)
    if estado:
        query = query.filter(Weighing.estado == estado)

    total    = query.count()
    weighings = (
        query.options(
            selectinload(Weighing.recycler),
            selectinload(Weighing.material),
            selectinload(Weighing.warehouse),
        )
        .order_by(Weighing.fecha.desc(), Weighing.id)
        .offset(offset)
        .limit(limit)
        .all()
    )
    return WeighingListResponse(total=total, items=weighings)


@router.get("/stats", response_model=WeighingStatsResponse)
def weighing_stats(
    db: Session = Depends(get_db),
    _:  User    = Depends(require_roles(*WEIGHINGS_READ)),
):
    now   = datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    this_month = Weighing.fecha >= start

    count_month, total_kg = db.query(
        func.count(Weighing.id), func.coalesce(func.sum(Weighing.kg), 0)
    ).filter(this_month).one()
    pending = db.query(func.count(Weighing.id)).filter(Weighing.estado == WeighingStatus.pendiente).scalar()
    by_material = (
        db.query(Weighing.material_code, func.sum(Weighing.kg))
        .filter(this_month)
        .group_by(Weighing.material_code)
        .order_by(Weighing.material_code)
        .all()
    )

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
    recycler = db.get(User, request.recycler_id)
    if not recycler or recycler.user_type_code != "recycler":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reciclador no encontrado")
    weighing_service.ensure_recycler_can_deliver(recycler)

    if not db.get(Material, request.material_code):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Material no encontrado")

    if not db.get(Warehouse, request.warehouse_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bodega no encontrada")

    weighing = Weighing(
        id=uuid.uuid4(),
        recycler_id=request.recycler_id,
        material_code=request.material_code,
        warehouse_id=request.warehouse_id,
        kg=request.kg,
        precio_kg=request.precio_kg,
    )
    db.add(weighing)
    audit.record(
        db, Action.WEIGHING_CREATED, actor=actor, target_type="weighing", target_id=weighing.id,
        details={"recycler_id": str(request.recycler_id), "material": request.material_code,
                 "kg": str(request.kg), "precio_kg": str(request.precio_kg)})
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
    # A recycler asking for someone else's weighing gets the same answer as for a missing one.
    if not weighing or (actor.user_type_code == "recycler" and weighing.recycler_id != actor.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pesaje no encontrado")
    return weighing


@router.patch("/{weighing_id}/status", response_model=WeighingResponse)
def update_weighing_status(
    weighing_id: uuid.UUID,
    request:     UpdateWeighingStatusRequest,
    db:          Session = Depends(get_db),
    current_user: User   = Depends(get_current_user),
):
    # Paying moves money; validating/rejecting is the review step.
    ensure_role(current_user, PAYMENTS if request.status == WeighingStatus.pagado else WEIGHINGS_REVIEW)

    # FOR UPDATE: a concurrent transition on the same weighing waits here and then sees the new state.
    weighing = db.query(Weighing).filter(Weighing.id == weighing_id).with_for_update().first()
    if not weighing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pesaje no encontrado")

    previous = weighing.estado.value

    if request.status == WeighingStatus.validado:
        weighing_service.validate_weighing(db, weighing, current_user.id)
    elif request.status == WeighingStatus.rechazado:
        if not request.rejection_reason:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="rejection_reason es requerido al rechazar",
            )
        weighing_service.reject_weighing(db, weighing, request.rejection_reason)
    elif request.status == WeighingStatus.pagado:
        weighing_service.mark_paid(db, weighing)
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Transición de estado no válida")

    audit.record(
        db,
        {WeighingStatus.validado: Action.WEIGHING_VALIDATED, WeighingStatus.rechazado: Action.WEIGHING_REJECTED,
         WeighingStatus.pagado: Action.WEIGHING_PAID}[request.status],
        actor=current_user, target_type="weighing", target_id=weighing.id,
        details={"from": previous, "material": weighing.material_code, "kg": str(weighing.kg),
                 "precio_kg": str(weighing.precio_kg), "recycler_id": str(weighing.recycler_id)})
    db.commit()
    db.refresh(weighing)
    return weighing
