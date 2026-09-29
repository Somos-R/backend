import uuid
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.domains.inventory import service as inventory_service
from app.domains.transactions.models import (
    Transaction,
    TransactionStatus,
    TransactionType,
)
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
        raise HTTPException(
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Solo se pueden marcar como entregadas ventas en estado 'pending'",
        )
    transaction.status = TransactionStatus.delivered
    return transaction
