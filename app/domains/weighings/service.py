import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import status
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.domains.inventory import service as inventory_service
from app.domains.users.models import User
from app.domains.weighings.models import Weighing, WeighingStatus


def ensure_recycler_is_active(recycler: User) -> None:
    """An ECA receives material whoever brings it, verified or not, from any association or none. The only
    exception is an account that Somos R itself has deactivated.

    Checked when the weighing is registered and again when it is validated, because the account may have
    been deactivated in between (validating creates a purchase that is owed to them).
    """
    if not recycler.is_active:
        raise ApiError(
            "recycler_inactive",
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La cuenta del reciclador está desactivada",
        )


def validate_weighing(db: Session, weighing: Weighing, validator_id: uuid.UUID) -> Weighing:
    """
    Transitions a weighing to 'validated'.
    Side effects:
      - Adds stock to inventory_items for the material + warehouse.
      - Creates a purchase Transaction linked to this weighing.
    """
    if weighing.status != WeighingStatus.pending_validation:
        raise ApiError("invalid_transition", 
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Solo se pueden validar pesajes en estado 'pending_validation'. Estado actual: {weighing.status}",
        )
    if weighing.recycler is not None:
        ensure_recycler_is_active(weighing.recycler)

    weighing.status        = WeighingStatus.validated
    weighing.validated_by  = validator_id
    weighing.validated_at  = datetime.now(timezone.utc)
    weighing.updated_at    = datetime.now(timezone.utc)

    # Side effect: update inventory
    inventory_service.add_stock(
        db=db,
        material_code=weighing.material_code,
        warehouse_id=weighing.warehouse_id,
        kg=Decimal(str(weighing.kg)),
        price_per_kg=Decimal(str(weighing.price_per_kg)),
    )

    # Side effect: create purchase transaction (imported here to avoid circular imports at module load)
    from app.domains.transactions import service as tx_service
    tx_service.create_purchase_from_weighing(db=db, weighing=weighing, created_by=validator_id)

    return weighing


def reject_weighing(db: Session, weighing: Weighing, reason: str) -> Weighing:
    if weighing.status != WeighingStatus.pending_validation:
        raise ApiError("invalid_transition", 
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Solo se pueden rechazar pesajes en estado 'pending_validation'",
        )
    weighing.status           = WeighingStatus.rejected
    weighing.rejection_reason = reason
    weighing.updated_at       = datetime.now(timezone.utc)
    return weighing


def mark_paid(db: Session, weighing: Weighing) -> Weighing:
    if weighing.status != WeighingStatus.validated:
        raise ApiError("invalid_transition", 
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Solo se pueden marcar como pagados pesajes en estado 'validated'",
        )
    weighing.status     = WeighingStatus.paid
    weighing.updated_at = datetime.now(timezone.utc)
    return weighing
