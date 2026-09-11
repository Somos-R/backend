import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.domains.inventory.models import Material, Warehouse
from app.domains.transactions import service as tx_service
from app.domains.transactions.models import Transaction, TransactionStatus, TransactionType
from app.domains.transactions.schemas import (
    CreateVentaRequest,
    TransactionListResponse,
    TransactionResponse,
    TransactionStatsResponse,
    UpdateTransactionStatusRequest,
)
from app.domains.users.models import User

router = APIRouter(prefix="/transactions", tags=["transactions"])


@router.get("", response_model=TransactionListResponse)
def list_transactions(
    type:        TransactionType | None   = Query(default=None),
    status_:     TransactionStatus | None = Query(default=None, alias="status"),
    material_code: str | None            = Query(default=None),
    limit:       int                     = Query(default=20, ge=1, le=100),
    offset:      int                     = Query(default=0, ge=0),
    db:          Session                 = Depends(get_db),
    _:           User                    = Depends(get_current_user),
):
    query = db.query(Transaction)
    if type:
        query = query.filter(Transaction.type == type)
    if status_:
        query = query.filter(Transaction.status == status_)
    if material_code:
        query = query.filter(Transaction.material_code == material_code)

    total = query.count()
    items = query.order_by(Transaction.fecha.desc()).offset(offset).limit(limit).all()
    return TransactionListResponse(total=total, items=items)


@router.get("/stats", response_model=TransactionStatsResponse)
def transaction_stats(
    db: Session = Depends(get_db),
    _:  User    = Depends(get_current_user),
):
    now   = datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    month_txs = db.query(Transaction).filter(Transaction.fecha >= start).all()

    compras = [t for t in month_txs if t.type == TransactionType.compra]
    ventas  = [t for t in month_txs if t.type == TransactionType.venta]

    from decimal import Decimal
    return TransactionStatsResponse(
        total_compras_month  = len(compras),
        total_ventas_month   = len(ventas),
        total_kg_compras     = sum((t.kg for t in compras), Decimal("0")),
        total_kg_ventas      = sum((t.kg for t in ventas), Decimal("0")),
        total_value_compras  = sum((t.total_value for t in compras), Decimal("0")),
        total_value_ventas   = sum((t.total_value for t in ventas), Decimal("0")),
        pending_count        = sum(1 for t in month_txs if t.status == TransactionStatus.pendiente),
    )


@router.post("", response_model=TransactionResponse, status_code=status.HTTP_201_CREATED)
def create_venta(
    request:      CreateVentaRequest,
    db:           Session = Depends(get_db),
    current_user: User    = Depends(get_current_user),
):
    if not db.get(Material, request.material_code):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Material no encontrado")
    if not db.get(Warehouse, request.warehouse_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bodega no encontrada")

    tx = tx_service.create_venta(
        db=db,
        material_code=request.material_code,
        warehouse_id=request.warehouse_id,
        kg=request.kg,
        precio_kg=request.precio_kg,
        created_by=current_user.id,
        buyer_name=request.buyer_name,
        buyer_nit=request.buyer_nit,
        buyer_email=request.buyer_email,
    )
    db.commit()
    db.refresh(tx)
    return tx


@router.get("/{transaction_id}", response_model=TransactionResponse)
def get_transaction(
    transaction_id: uuid.UUID,
    db:             Session = Depends(get_db),
    _:              User    = Depends(get_current_user),
):
    tx = db.get(Transaction, transaction_id)
    if not tx:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transacción no encontrada")
    return tx


@router.patch("/{transaction_id}/status", response_model=TransactionResponse)
def update_status(
    transaction_id: uuid.UUID,
    request:        UpdateTransactionStatusRequest,
    db:             Session = Depends(get_db),
    _:              User    = Depends(get_current_user),
):
    tx = db.get(Transaction, transaction_id)
    if not tx:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transacción no encontrada")

    if request.status == TransactionStatus.cancelado:
        tx_service.cancel_transaction(db, tx)
    elif request.status == TransactionStatus.entregado:
        tx_service.mark_entregado(db, tx)
    elif request.status == TransactionStatus.pagado:
        if tx.type != TransactionType.compra or tx.status != TransactionStatus.pendiente:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Transición no válida")
        tx.status = TransactionStatus.pagado
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Transición de estado no soportada")

    db.commit()
    db.refresh(tx)
    return tx
