import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import status
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.domains.inventory import service as inventory_service
from app.domains.users.enums import VerificationStatus
from app.domains.users.models import User
from app.domains.weighings.models import Weighing, WeighingStatus


def ensure_recycler_can_deliver(recycler: User | None) -> None:
    """A weighing needs a recycler who is verified and whose account is active.

    Checked when the weighing is registered and again when it is validated, because the
    recycler may have been rejected or deactivated in between (validating creates a purchase
    that is owed to them).
    """
    if (
        recycler is None
        or not recycler.is_active
        or recycler.verification_status != VerificationStatus.verified
    ):
        raise ApiError("recycler_not_verified", 
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El reciclador no está verificado o su cuenta está desactivada",
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
    ensure_recycler_can_deliver(weighing.recycler)

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
