"""The integrity hash of a weighing: a SHA-256 over what was recorded when it was registered.

The weighing of the ECA is the single source of value, so what was weighed must stay detectable if anyone changes it
later. The hash is computed on the server when the weighing is registered and never comes from the client.

This is a plain SHA-256, not a keyed one (HMAC): it detects accidental or partial changes, but whoever can write to
the database and knows this format could recompute the hash. Do not present it as proof of integrity before third
parties; moving to HMAC with a server key means a new `CANONICAL_VERSION` and is cheaper now than once hashes exist.

It covers the facts of the weighing (who delivered, what, how much, where and when), not what happens to it
afterwards: validating, rejecting or paying a weighing changes its status, never these fields. A correction is a new
linked record, never an edit of this one.
"""
import hashlib
import hmac
import json
from datetime import timezone
from decimal import Decimal

from app.domains.weighings.models import Weighing

# The version of the canonical form below. Change it (and keep the old one readable) if the fields ever change.
CANONICAL_VERSION = 1
ALGORITHM = "sha256"


def _fixed(value: Decimal) -> str:
    """kg and price are stored with two decimals: always write them the same way."""
    return format(value.quantize(Decimal("0.01")), "f")


def canonical_payload(weighing: Weighing) -> bytes:
    """The same bytes for the same weighing: fixed field order, normalized dates and decimals."""
    fields = {
        "version": CANONICAL_VERSION,
        "id": str(weighing.id),
        "recycler_id": str(weighing.recycler_id) if weighing.recycler_id else None,
        "seller_name": weighing.seller_name,
        "seller_id_type": weighing.seller_id_type,
        "seller_id_number": weighing.seller_id_number,
        "affiliation_status": weighing.affiliation_status.value,
        "material_code": weighing.material_code,
        "warehouse_id": str(weighing.warehouse_id),
        "kg": _fixed(weighing.kg),
        "price_per_kg": _fixed(weighing.price_per_kg),
        "occurred_at": weighing.occurred_at.astimezone(timezone.utc).isoformat(timespec="microseconds"),
    }
    return json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def compute_hash(weighing: Weighing) -> str:
    return hashlib.sha256(canonical_payload(weighing)).hexdigest()


def matches(weighing: Weighing) -> bool | None:
    """Whether the stored hash still matches the weighing. None when it has none (registered before hashing)."""
    if weighing.record_hash is None:
        return None
    return hmac.compare_digest(weighing.record_hash, compute_hash(weighing))
