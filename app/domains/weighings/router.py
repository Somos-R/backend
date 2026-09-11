import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.domains.inventory.models import Material, Warehouse
from app.domains.users.models import User
from app.domains.weighings.models import Weighing, WeighingStatus
from app.domains.weighings.schemas import (
    CreateWeighingRequest,
    UpdateWeighingStatusRequest,
    WeighingListResponse,
    WeighingResponse,
    WeighingStatsResponse,
)
from app.domains.weighings import service as weighing_service

router = APIRouter(prefix="/weighings", tags=["weighings"])


@router.get("", response_model=WeighingListResponse)
def list_weighings(
    recycler_id:   uuid.UUID | None = Query(default=None),
    material_code: str | None       = Query(default=None),
    warehouse_id:  uuid.UUID | None = Query(default=None),
    estado:        str | None       = Query(default=None),
    limit:         int              = Query(default=20, ge=1, le=100),
    offset:        int              = Query(default=0, ge=0),
    db:            Session          = Depends(get_db),
    _:             User             = Depends(get_current_user),
):
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
    weighings = query.order_by(Weighing.fecha.desc()).offset(offset).limit(limit).all()
    return WeighingListResponse(total=total, items=weighings)


@router.get("/stats", response_model=WeighingStatsResponse)
def weighing_stats(
    db: Session = Depends(get_db),
    _:  User    = Depends(get_current_user),
):
    now   = datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    month_weighings = (
        db.query(Weighing)
        .filter(Weighing.fecha >= start)
        .all()
    )

    total_kg    = sum(w.kg for w in month_weighings)
    pending     = db.query(Weighing).filter(Weighing.estado == WeighingStatus.pendiente).count()

    by_material: dict[str, float] = {}
    for w in month_weighings:
        by_material[w.material_code] = by_material.get(w.material_code, 0.0) + float(w.kg)

    return WeighingStatsResponse(
        total_weighings_month=len(month_weighings),
        total_kg_month=total_kg,
        pending_count=pending,
        by_material=[{"material": k, "kg": v} for k, v in by_material.items()],
    )


@router.post("", response_model=WeighingResponse, status_code=status.HTTP_201_CREATED)
def create_weighing(
    request: CreateWeighingRequest,
    db:      Session = Depends(get_db),
    _:       User    = Depends(get_current_user),
):
    recycler = db.get(User, request.recycler_id)
    if not recycler or recycler.user_type_code != "recycler":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reciclador no encontrado")

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
    db.commit()
    db.refresh(weighing)
    return weighing


@router.get("/{weighing_id}", response_model=WeighingResponse)
def get_weighing(
    weighing_id: uuid.UUID,
    db:          Session = Depends(get_db),
    _:           User    = Depends(get_current_user),
):
    weighing = db.get(Weighing, weighing_id)
    if not weighing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pesaje no encontrado")
    return weighing


@router.patch("/{weighing_id}/status", response_model=WeighingResponse)
def update_weighing_status(
    weighing_id: uuid.UUID,
    request:     UpdateWeighingStatusRequest,
    db:          Session = Depends(get_db),
    current_user: User   = Depends(get_current_user),
):
    weighing = db.get(Weighing, weighing_id)
    if not weighing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pesaje no encontrado")

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

    db.commit()
    db.refresh(weighing)
    return weighing
