import uuid
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class WeighingValidated:
    """A weighing passed review: the material is now in the warehouse and a purchase is owed.

    Carries plain values, not the ORM row, so whoever reacts depends on this contract and not on the model.
    """

    weighing_id: uuid.UUID
    recycler_id: uuid.UUID | None  # None: an unregistered seller
    material_code: str
    warehouse_id: uuid.UUID
    kg: Decimal
    price_per_kg: Decimal
    validated_by: uuid.UUID
