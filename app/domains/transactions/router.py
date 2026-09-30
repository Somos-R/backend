import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.permissions import (
    PAYMENTS,
    TRANSACTIONS_READ,
    TRANSACTIONS_WRITE,
    ensure_role,
)
from app.core.security import get_current_user, require_roles
from app.domains.transactions import service as tx_service
from app.domains.transactions.models import TransactionStatus, TransactionType
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
    actor:       User                    = Depends(require_roles(*TRANSACTIONS_READ)),
):
    total, items = tx_service.list_transactions(
        db, actor, type_=type, status_=status_, material_code=material_code, limit=limit, offset=offset)
    return TransactionListResponse(total=total, items=items)


@router.get("/stats", response_model=TransactionStatsResponse)
def transaction_stats(
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*TRANSACTIONS_READ)),
):
    per_type, pending = tx_service.month_stats(db, actor)
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
    return tx_service.register_sale(db, current_user, request)


@router.get("/{transaction_id}", response_model=TransactionResponse)
def get_transaction(
    transaction_id: uuid.UUID,
    db:             Session = Depends(get_db),
    actor:          User    = Depends(require_roles(*TRANSACTIONS_READ)),
):
    return tx_service.get_transaction(db, actor, transaction_id)


@router.patch("/{transaction_id}/status", response_model=TransactionResponse)
def update_status(
    transaction_id: uuid.UUID,
    request:        UpdateTransactionStatusRequest,
    db:             Session = Depends(get_db),
    actor:          User    = Depends(get_current_user),
):
    # Paying a purchase moves money; cancelling/delivering moves stock.
    ensure_role(actor, PAYMENTS if request.status == TransactionStatus.paid else TRANSACTIONS_WRITE)
    return tx_service.update_status(db, actor, transaction_id, request)
