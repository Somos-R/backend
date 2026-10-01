import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import ApiError
from app.core.pagination import paginate
from app.domains.audit import service as audit
from app.domains.audit.actions import Action
from app.domains.inventory import service as inventory_service
from app.domains.organizations import scope
from app.domains.transactions.models import (
    Transaction,
    TransactionStatus,
    TransactionType,
)
from app.domains.transactions.schemas import (
    CreateSaleRequest,
    UpdateTransactionStatusRequest,
)
from app.domains.users.models import User
from app.domains.weighings.models import Weighing


def create_purchase_from_weighing(
    db: Session,
    weighing: Weighing,
    created_by: uuid.UUID,
) -> Transaction:
    """Creates a purchase Transaction linked to a validated weighing. Caller must commit."""
    tx = Transaction(
        id=uuid.uuid4(),
        type=TransactionType.purchase,
        status=TransactionStatus.pending,
        material_code=weighing.material_code,
        warehouse_id=weighing.warehouse_id,
        kg=weighing.kg,
        price_per_kg=weighing.price_per_kg,
        recycler_id=weighing.recycler_id,
        weighing_id=weighing.id,
        created_by=created_by,
    )
    db.add(tx)
    db.flush()
    return tx


def create_sale(
    db: Session,
    material_code: str,
    warehouse_id: uuid.UUID,
    kg: Decimal,
    price_per_kg: Decimal,
    created_by: uuid.UUID,
    buyer_name: str | None = None,
    buyer_nit: str | None = None,
    buyer_email: str | None = None,
) -> Transaction:
    """Creates a sale Transaction and subtracts the sold stock from inventory.

    Caller must commit. Raises HTTPException if there is not enough stock.
    """
    inventory_service.subtract_stock(
        db=db,
        material_code=material_code,
        warehouse_id=warehouse_id,
        kg=kg,
    )

    tx = Transaction(
        id=uuid.uuid4(),
        type=TransactionType.sale,
        status=TransactionStatus.pending,
        material_code=material_code,
        warehouse_id=warehouse_id,
        kg=kg,
        price_per_kg=price_per_kg,
        buyer_name=buyer_name,
        buyer_nit=buyer_nit,
        buyer_email=buyer_email,
        created_by=created_by,
    )
    db.add(tx)
    db.flush()
    return tx


def cancel_transaction(db: Session, transaction: Transaction) -> Transaction:
    if transaction.status != TransactionStatus.pending:
        raise ApiError("invalid_transition", 
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Solo se pueden cancelar transacciones en estado 'pending'",
        )
    if transaction.type == TransactionType.sale:
        # Stock was deducted when the sale was created — restore it.
        inventory_service.add_stock(
            db=db,
            material_code=transaction.material_code,
            warehouse_id=transaction.warehouse_id,
            kg=transaction.kg,
            price_per_kg=None,  # returning stock must not reprice the existing inventory
        )
    transaction.status = TransactionStatus.cancelled
    return transaction


def mark_delivered(db: Session, transaction: Transaction) -> Transaction:
    if transaction.type != TransactionType.sale or transaction.status != TransactionStatus.pending:
        raise ApiError("invalid_transition", 
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Solo se pueden marcar como entregadas ventas en estado 'pending'",
        )
    transaction.status = TransactionStatus.delivered
    return transaction


def list_transactions(
    db: Session,
    actor: User,
    *,
    type_: TransactionType | None,
    status_: TransactionStatus | None,
    material_code: str | None,
    limit: int,
    offset: int,
) -> tuple[int, list[Transaction]]:
    query = select(Transaction).where(scope.transaction_scope(actor))
    if type_:
        query = query.where(Transaction.type == type_)
    if status_:
        query = query.where(Transaction.status == status_)
    if material_code:
        query = query.where(Transaction.material_code == material_code)

    return paginate(
        db, query, Transaction.occurred_at.desc(), Transaction.id, limit=limit, offset=offset,
        options=(
            selectinload(Transaction.material),
            selectinload(Transaction.warehouse),
            selectinload(Transaction.recycler),
        ),
    )


def month_stats(db: Session, actor: User) -> tuple[dict[TransactionType, tuple[int, Decimal, Decimal]], int]:
    """(count, kg, value) per type this month and the pending count, all in SQL."""
    now = datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    this_month = Transaction.occurred_at >= start
    mine = scope.transaction_scope(actor)

    per_type = {
        tx_type: (count, kg, value)
        for tx_type, count, kg, value in db.execute(
            select(
                Transaction.type,
                func.count(Transaction.id),
                func.coalesce(func.sum(Transaction.kg), 0),
                func.coalesce(func.sum(Transaction.kg * Transaction.price_per_kg), 0),
            ).where(this_month, mine).group_by(Transaction.type)
        ).all()
    }
    pending = db.scalar(
        select(func.count(Transaction.id)).where(this_month, mine, Transaction.status == TransactionStatus.pending)
    ) or 0
    return per_type, pending


def register_sale(db: Session, actor: User, request: CreateSaleRequest) -> Transaction:
    inventory_service.ensure_material_is_active(db, request.material_code)
    scope.get_own_warehouse(db, actor, request.warehouse_id)  # a sale is out of the ECA's own warehouse

    tx = create_sale(
        db=db,
        material_code=request.material_code,
        warehouse_id=request.warehouse_id,
        kg=request.kg,
        price_per_kg=request.price_per_kg,
        created_by=actor.id,
        buyer_name=request.buyer_name,
        buyer_nit=request.buyer_nit,
        buyer_email=request.buyer_email,
    )
    audit.record(
        db, Action.TRANSACTION_CREATED, actor=actor, target_type="transaction", target_id=tx.id,
        details={"type": "sale", "material": request.material_code, "kg": str(request.kg),
                 "price_per_kg": str(request.price_per_kg)})
    db.commit()
    db.refresh(tx)
    return tx


def get_transaction(db: Session, actor: User, transaction_id: uuid.UUID) -> Transaction:
    tx = db.scalars(select(Transaction).where(
        Transaction.id == transaction_id, scope.transaction_scope(actor))).first()
    if not tx:
        raise ApiError("transaction_not_found", status_code=status.HTTP_404_NOT_FOUND, detail="Transacción no encontrada")
    return tx


def update_status(db: Session, actor: User, transaction_id: uuid.UUID, request: UpdateTransactionStatusRequest) -> Transaction:
    # FOR UPDATE: two concurrent cancels must not both restore the stock.
    tx = db.scalars(select(Transaction).where(
        Transaction.id == transaction_id, scope.transaction_scope(actor)).with_for_update()).first()
    if not tx:
        raise ApiError("transaction_not_found", status_code=status.HTTP_404_NOT_FOUND, detail="Transacción no encontrada")

    previous = tx.status.value

    if request.status == TransactionStatus.cancelled:
        cancel_transaction(db, tx)
    elif request.status == TransactionStatus.delivered:
        mark_delivered(db, tx)
    elif request.status == TransactionStatus.paid:
        if tx.type != TransactionType.purchase or tx.status != TransactionStatus.pending:
            raise ApiError("invalid_transition", status_code=status.HTTP_400_BAD_REQUEST, detail="Transición no válida")
        tx.status = TransactionStatus.paid
    else:
        raise ApiError("invalid_transition", status_code=status.HTTP_400_BAD_REQUEST, detail="Transición de estado no soportada")

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
