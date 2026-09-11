import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.domains.inventory import service as inventory_service
from app.domains.weighings.models import Weighing, WeighingStatus


def validate_weighing(db: Session, weighing: Weighing, validator_id: uuid.UUID) -> Weighing:
    """
    Transitions a weighing to 'validado'.
    Side effects:
      - Adds stock to inventory_items for the material + warehouse.
      - Creates a compra Transaction linked to this weighing.
    """
    if weighing.estado != WeighingStatus.pendiente:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Solo se pueden validar pesajes en estado 'pendiente'. Estado actual: {weighing.estado}",
        )

    weighing.estado        = WeighingStatus.validado
    weighing.validated_by  = validator_id
    weighing.validated_at  = datetime.now(timezone.utc)
    weighing.updated_at    = datetime.now(timezone.utc)

    # Side effect: update inventory
    inventory_service.add_stock(
        db=db,
        material_code=weighing.material_code,
        warehouse_id=weighing.warehouse_id,
        kg=Decimal(str(weighing.kg)),
        precio_kg=Decimal(str(weighing.precio_kg)),
    )

    # Side effect: create compra transaction (imported here to avoid circular imports at module load)
    from app.domains.transactions import service as tx_service
    tx_service.create_compra_from_weighing(db=db, weighing=weighing, created_by=validator_id)

    return weighing


def reject_weighing(db: Session, weighing: Weighing, reason: str) -> Weighing:
    if weighing.estado != WeighingStatus.pendiente:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Solo se pueden rechazar pesajes en estado 'pendiente'",
        )
    weighing.estado           = WeighingStatus.rechazado
    weighing.rejection_reason = reason
    weighing.updated_at       = datetime.now(timezone.utc)
    return weighing


def mark_paid(db: Session, weighing: Weighing) -> Weighing:
    if weighing.estado != WeighingStatus.validado:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Solo se pueden marcar como pagados pesajes en estado 'validado'",
        )
    weighing.estado     = WeighingStatus.pagado
    weighing.updated_at = datetime.now(timezone.utc)
    return weighing
