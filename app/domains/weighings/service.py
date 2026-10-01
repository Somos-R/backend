import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core import search
from app.core.errors import ApiError
from app.core.pagination import paginate
from app.core.permissions import forbidden
from app.domains.audit import service as audit
from app.domains.audit.actions import Action
from app.domains.catalogs.service import ensure_document_type_is_active
from app.domains.inventory import service as inventory_service
from app.domains.inventory.service import ensure_material_is_active
from app.domains.organizations import scope
from app.domains.users.models import User
from app.domains.weighings.models import AffiliationStatus, Weighing, WeighingStatus
from app.domains.weighings.schemas import (
    CreateWeighingRequest,
    UpdateWeighingStatusRequest,
)


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


SORT_COLUMNS = {
    "occurred_at": Weighing.occurred_at,
    "kg": Weighing.kg,
    "price_per_kg": Weighing.price_per_kg,
    "total_value": Weighing.kg * Weighing.price_per_kg,
    "status": Weighing.status,
}
MAX_EXPORT_ROWS = 10_000
_RELATIONS = (selectinload(Weighing.recycler), selectinload(Weighing.material), selectinload(Weighing.warehouse))


def _visible_weighings(
    actor: User,
    *,
    recycler_id: uuid.UUID | None,
    material_code: str | None,
    warehouse_id: uuid.UUID | None,
    status_: WeighingStatus | None,
    affiliation: AffiliationStatus | None,
    q: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
):
    """The weighings the actor may read, narrowed by the filters; a recycler only ever gets their own."""
    if actor.user_type_code == "recycler":
        if recycler_id and recycler_id != actor.id:
            raise forbidden()
        recycler_id = actor.id

    query = select(Weighing)
    if actor.user_type_code != "recycler":
        query = query.where(scope.weighing_scope(actor))

    if recycler_id:
        query = query.where(Weighing.recycler_id == recycler_id)
    if material_code:
        query = query.where(Weighing.material_code == material_code)
    if warehouse_id:
        query = query.where(Weighing.warehouse_id == warehouse_id)
    if status_:
        query = query.where(Weighing.status == status_)
    if affiliation:
        query = query.where(Weighing.affiliation_status == affiliation)
    if date_from:
        query = query.where(Weighing.occurred_at >= date_from)
    if date_to:
        query = query.where(Weighing.occurred_at <= date_to)
    # Text search over who delivered: the registered recycler's name and document, or the name and document
    # of an unregistered seller. It narrows the list; it never widens what the actor may already see.
    recycler_match = search.contains(q, [User.full_name, User.id_number])
    seller_match = search.contains(q, [Weighing.seller_name, Weighing.seller_id_number])
    if recycler_match is not None and seller_match is not None:
        query = query.where(or_(Weighing.recycler_id.in_(select(User.id).where(recycler_match)), seller_match))
    return query


def _ordering(sort: str, descending: bool) -> tuple:
    column = SORT_COLUMNS[sort]
    # `id` last keeps pages stable when many rows share the sorted value.
    return (column.desc() if descending else column.asc(), Weighing.id)


def list_weighings(
    db: Session,
    actor: User,
    *,
    recycler_id: uuid.UUID | None,
    material_code: str | None,
    warehouse_id: uuid.UUID | None,
    status_: WeighingStatus | None,
    affiliation: AffiliationStatus | None,
    q: str | None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    sort: str = "occurred_at",
    descending: bool = True,
    limit: int,
    offset: int,
) -> tuple[int, list[Weighing]]:
    """One page of what the actor may read, in the requested order."""
    query = _visible_weighings(
        actor, recycler_id=recycler_id, material_code=material_code, warehouse_id=warehouse_id,
        status_=status_, affiliation=affiliation, q=q, date_from=date_from, date_to=date_to)
    return paginate(db, query, *_ordering(sort, descending), limit=limit, offset=offset, options=_RELATIONS)


def export_weighings(
    db: Session,
    actor: User,
    *,
    recycler_id: uuid.UUID | None,
    material_code: str | None,
    warehouse_id: uuid.UUID | None,
    status_: WeighingStatus | None,
    affiliation: AffiliationStatus | None,
    q: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
    sort: str,
    descending: bool,
) -> list[Weighing]:
    """Every weighing matching the filters (not one page), for a file. Refuses rather than truncating: a
    silently partial report would be taken for the whole."""
    query = _visible_weighings(
        actor, recycler_id=recycler_id, material_code=material_code, warehouse_id=warehouse_id,
        status_=status_, affiliation=affiliation, q=q, date_from=date_from, date_to=date_to)
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    if total > MAX_EXPORT_ROWS:
        raise ApiError(
            "export_too_large", status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"El reporte tiene {total} pesajes; el máximo es {MAX_EXPORT_ROWS}. Acote el periodo o los filtros")
    rows = db.scalars(query.options(*_RELATIONS).order_by(*_ordering(sort, descending))).all()
    audit.record(db, Action.WEIGHINGS_EXPORTED, actor=actor, target_type="weighing", target_id=None,
                 details={"rows": len(rows)})
    db.commit()
    return list(rows)


def month_stats(db: Session, actor: User) -> tuple[int, Decimal, int, list[tuple[str, Decimal]]]:
    """(weighings this month, kg this month, pending validation, kg by material this month), all in SQL."""
    now = datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    this_month = Weighing.occurred_at >= start
    mine = scope.weighing_scope(actor)

    count_month, total_kg = db.execute(
        select(func.count(Weighing.id), func.coalesce(func.sum(Weighing.kg), 0)).where(this_month, mine)
    ).one()
    pending = db.scalar(
        select(func.count(Weighing.id)).where(Weighing.status == WeighingStatus.pending_validation, mine)
    ) or 0
    by_material = db.execute(
        select(Weighing.material_code, func.sum(Weighing.kg))
        .where(this_month, mine)
        .group_by(Weighing.material_code)
        .order_by(Weighing.material_code)
    ).all()
    return count_month, total_kg, pending, [(material, kg) for material, kg in by_material]


def create_weighing(db: Session, actor: User, request: CreateWeighingRequest) -> Weighing:
    recycler = None
    if request.recycler_id is not None:
        recycler = db.get(User, request.recycler_id)
        if not recycler or recycler.user_type_code != "recycler":
            raise ApiError("recycler_not_found", status_code=status.HTTP_404_NOT_FOUND, detail="Reciclador no encontrado")
        ensure_recycler_is_active(recycler)
    elif request.seller is not None:
        ensure_document_type_is_active(db, request.seller.id_type)

    ensure_material_is_active(db, request.material_code)

    # Only in a warehouse of the actor's own ECA. The ECA receives the material whoever brings it; how the
    # seller relates to the ECA (linked, another association's, independent) is recorded, and decides
    # whether the weighing reaches an association.
    warehouse = scope.get_own_warehouse(db, actor, request.warehouse_id)
    affiliation = (
        scope.affiliation_of(db, warehouse.organization_id, recycler) if recycler is not None
        else AffiliationStatus.independent)
    seller = request.seller

    weighing = Weighing(
        id=uuid.uuid4(),
        recycler_id=request.recycler_id,
        seller_name=seller.full_name.strip() if seller else None,
        seller_id_type=seller.id_type if seller else None,
        seller_id_number=seller.id_number if seller else None,
        affiliation_status=affiliation,
        material_code=request.material_code,
        warehouse_id=request.warehouse_id,
        kg=request.kg,
        price_per_kg=request.price_per_kg,
    )
    db.add(weighing)
    audit.record(
        db, Action.WEIGHING_CREATED, actor=actor, target_type="weighing", target_id=weighing.id,
        details={"recycler_id": str(request.recycler_id) if request.recycler_id else None,
                 "walk_in": seller is not None, "affiliation": affiliation.value,
                 "material": request.material_code, "kg": str(request.kg), "price_per_kg": str(request.price_per_kg)})
    db.commit()
    db.refresh(weighing)
    return weighing


def get_weighing(db: Session, actor: User, weighing_id: uuid.UUID) -> Weighing:
    """The weighing if the actor may read it. Someone else's weighing gets the same answer as a missing
    one: a recycler's peers, another ECA, another association."""
    weighing = db.get(Weighing, weighing_id)
    outside = (
        weighing is not None
        and (weighing.recycler_id != actor.id if actor.user_type_code == "recycler"
             else db.scalar(select(Weighing.id).where(Weighing.id == weighing_id, scope.weighing_scope(actor))) is None)
    )
    if not weighing or outside:
        raise ApiError("weighing_not_found", status_code=status.HTTP_404_NOT_FOUND, detail="Pesaje no encontrado")
    return weighing


def update_status(db: Session, actor: User, weighing_id: uuid.UUID, request: UpdateWeighingStatusRequest) -> Weighing:
    # FOR UPDATE: a concurrent transition on the same weighing waits here and then sees the new state.
    weighing = db.scalars(
        select(Weighing).where(Weighing.id == weighing_id, scope.weighing_scope(actor)).with_for_update()
    ).first()
    if not weighing:
        raise ApiError("weighing_not_found", status_code=status.HTTP_404_NOT_FOUND, detail="Pesaje no encontrado")

    previous = weighing.status.value

    if request.status == WeighingStatus.validated:
        validate_weighing(db, weighing, actor.id)
    elif request.status == WeighingStatus.rejected:
        if not request.rejection_reason:
            raise ApiError(
                "rejection_reason_required",
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="rejection_reason es requerido al rechazar",
            )
        reject_weighing(db, weighing, request.rejection_reason)
    elif request.status == WeighingStatus.paid:
        mark_paid(db, weighing)
    else:
        raise ApiError("invalid_transition", status_code=status.HTTP_400_BAD_REQUEST, detail="Transición de status no válida")

    audit.record(
        db,
        {WeighingStatus.validated: Action.WEIGHING_VALIDATED, WeighingStatus.rejected: Action.WEIGHING_REJECTED,
         WeighingStatus.paid: Action.WEIGHING_PAID}[request.status],
        actor=actor, target_type="weighing", target_id=weighing.id,
        details={"from": previous, "material": weighing.material_code, "kg": str(weighing.kg),
                 "price_per_kg": str(weighing.price_per_kg),
                 "recycler_id": str(weighing.recycler_id) if weighing.recycler_id else None})
    db.commit()
    db.refresh(weighing)
    return weighing
