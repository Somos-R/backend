import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
from app.core.permissions import (
    PAYMENTS,
    TRANSACTIONS_READ,
    TRANSACTIONS_WRITE,
    ensure_role,
)
from app.core.security import get_current_user, require_roles
from app.domains.audit import service as audit
from app.domains.audit.actions import Action
from app.domains.inventory.models import Material, Warehouse
from app.domains.transactions import service as tx_service
from app.domains.transactions.models import (
    Transaction,
    TransactionStatus,
    TransactionType,
)
from app.domains.transactions.schemas import (
    CreateSaleRequest,
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
    _:           User                    = Depends(require_roles(*TRANSACTIONS_READ)),
):
    query = db.query(Transaction)
    if type:
        query = query.filter(Transaction.type == type)
    if status_:
        query = query.filter(Transaction.status == status_)
    if material_code:
        query = query.filter(Transaction.material_code == material_code)

    total = query.count()
    items = (
        query.options(
            selectinload(Transaction.material),
            selectinload(Transaction.warehouse),
            selectinload(Transaction.recycler),
        )
        .order_by(Transaction.occurred_at.desc(), Transaction.id)
        .offset(offset)
        .limit(limit)
        .all()
    )
    return TransactionListResponse(total=total, items=items)


@router.get("/stats", response_model=TransactionStatsResponse)
def transaction_stats(
    db: Session = Depends(get_db),
    _:  User    = Depends(require_roles(*TRANSACTIONS_READ)),
):
    now   = datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    this_month = Transaction.occurred_at >= start

    per_type = {
        tx_type: (count, kg, value)
        for tx_type, count, kg, value in db.query(
            Transaction.type,
            func.count(Transaction.id),
            func.coalesce(func.sum(Transaction.kg), 0),
            func.coalesce(func.sum(Transaction.kg * Transaction.price_per_kg), 0),
        ).filter(this_month).group_by(Transaction.type).all()
    }
    pending = (
        db.query(func.count(Transaction.id))
        .filter(this_month, Transaction.status == TransactionStatus.pending)
        .scalar()
    )

    empty = (0, Decimal("0"), Decimal("0"))
    purchases = per_type.get(TransactionType.purchase, empty)
    sales = per_type.get(TransactionType.sale, empty)
    return TransactionStatsResponse(
        total_purchases_month  = purchases[0],
        total_sales_month   = sales[0],
        total_kg_purchases     = purchases[1],
        total_kg_sales      = sales[1],
        total_value_purchases  = purchases[2],
        total_value_sales   = sales[2],
        pending_count        = pending,
    )


@router.post("", response_model=TransactionResponse, status_code=status.HTTP_201_CREATED)
def create_sale(
    request:      CreateSaleRequest,
    db:           Session = Depends(get_db),
    current_user: User    = Depends(require_roles(*TRANSACTIONS_WRITE)),
):
    if not db.get(Material, request.material_code):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Material no encontrado")
    if not db.get(Warehouse, request.warehouse_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bodega no encontrada")

    tx = tx_service.create_sale(
        db=db,
        material_code=request.material_code,
        warehouse_id=request.warehouse_id,
        kg=request.kg,
        price_per_kg=request.price_per_kg,
        created_by=current_user.id,
        buyer_name=request.buyer_name,
        buyer_nit=request.buyer_nit,
        buyer_email=request.buyer_email,
    )
    audit.record(
        db, Action.TRANSACTION_CREATED, actor=current_user, target_type="transaction", target_id=tx.id,
        details={"type": "sale", "material": request.material_code, "kg": str(request.kg),
                 "price_per_kg": str(request.price_per_kg)})
    db.commit()
    db.refresh(tx)
    return tx


@router.get("/{transaction_id}", response_model=TransactionResponse)
def get_transaction(
    transaction_id: uuid.UUID,
    db:             Session = Depends(get_db),
    _:              User    = Depends(require_roles(*TRANSACTIONS_READ)),
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
    actor:          User    = Depends(get_current_user),
):
    # Paying a purchase moves money; cancelling/delivering moves stock.
    ensure_role(actor, PAYMENTS if request.status == TransactionStatus.paid else TRANSACTIONS_WRITE)

    # FOR UPDATE: two concurrent cancels must not both restore the stock.
    tx = db.query(Transaction).filter(Transaction.id == transaction_id).with_for_update().first()
    if not tx:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transacción no encontrada")

    previous = tx.status.value

    if request.status == TransactionStatus.cancelled:
        tx_service.cancel_transaction(db, tx)
    elif request.status == TransactionStatus.delivered:
        tx_service.mark_delivered(db, tx)
    elif request.status == TransactionStatus.paid:
        if tx.type != TransactionType.purchase or tx.status != TransactionStatus.pending:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Transición no válida")
        tx.status = TransactionStatus.paid
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Transición de estado no soportada")

    audit.record(
        db,
        {TransactionStatus.cancelled: Action.TRANSACTION_CANCELLED,
         TransactionStatus.delivered: Action.TRANSACTION_DELIVERED,
         TransactionStatus.paid: Action.TRANSACTION_PAID}[request.status],
        actor=actor, target_type="transaction", target_id=tx.id,
        details={"from": previous, "type": tx.type.value, "material": tx.material_code,
                 "kg": str(tx.kg), "price_per_kg": str(tx.price_per_kg)})
    db.commit()
    db.refresh(tx)
    return tx
