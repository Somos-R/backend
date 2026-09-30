import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
from app.core.errors import ApiError
from app.core.pagination import paginate
from app.core.permissions import ECA_ADMIN, INVENTORY_READ, INVENTORY_WRITE
from app.core.security import require_roles
from app.domains.audit import service as audit
from app.domains.audit.actions import Action
from app.domains.inventory.models import InventoryItem, Material, Warehouse
from app.domains.inventory.schemas import (
    CreateWarehouseRequest,
    InventoryItemResponse,
    InventoryListResponse,
    InventoryStatsResponse,
    MaterialResponse,
    UpdateInventoryItemRequest,
    WarehouseResponse,
)
from app.domains.organizations import scope
from app.domains.organizations.enums import OrganizationStatus
from app.domains.organizations.models import Organization
from app.domains.users.models import User

router = APIRouter(prefix="/inventory", tags=["inventory"])


@router.get("", response_model=InventoryListResponse)
def list_inventory(
    material_code: str | None = Query(default=None),
    warehouse_id: uuid.UUID | None = Query(default=None),
    status_: str | None = Query(default=None, alias="status", description="available | low_stock | out_of_stock"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*INVENTORY_READ)),
):
    query = select(InventoryItem).where(scope.inventory_scope(actor, InventoryItem.warehouse_id))

    if material_code:
        query = query.where(InventoryItem.material_code == material_code)
    if warehouse_id:
        query = query.where(InventoryItem.warehouse_id == warehouse_id)

    if status_:
        query = query.where(InventoryItem.status == status_)

    total, items = paginate(
        db, query,
        # warehouse_id breaks ties so that pages never overlap or skip rows
        InventoryItem.material_code, InventoryItem.warehouse_id,
        limit=limit, offset=offset,
        options=(selectinload(InventoryItem.material), selectinload(InventoryItem.warehouse)),
    )
    return InventoryListResponse(total=total, items=items)


@router.get("/stats", response_model=InventoryStatsResponse)
def inventory_stats(
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*INVENTORY_READ)),
):
    visible = scope.inventory_scope(actor, InventoryItem.warehouse_id)
    total_stock, total_value = db.execute(select(
        func.coalesce(func.sum(InventoryItem.stock_kg), 0),
        func.coalesce(func.sum(InventoryItem.stock_kg * InventoryItem.price_per_kg), 0),
    ).where(visible)).one()
    by_status: dict[str, int] = {
        status: count
        for status, count in db.execute(
            select(InventoryItem.status, func.count(InventoryItem.id)).where(visible).group_by(InventoryItem.status)
        ).all()
    }
    return InventoryStatsResponse(
        total_stock_kg=total_stock,
        total_value=total_value,
        available_count=by_status.get("available", 0),
        low_stock_count=by_status.get("low_stock", 0),
        out_of_stock_count=by_status.get("out_of_stock", 0),
    )


@router.get("/warehouses", response_model=list[WarehouseResponse])
def list_warehouses(
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*INVENTORY_READ)),
):
    return db.scalars(select(Warehouse).where(
        Warehouse.is_active.is_(True), scope.warehouse_scope(actor)).order_by(Warehouse.name, Warehouse.id)).all()


@router.post("/warehouses", response_model=WarehouseResponse, status_code=status.HTTP_201_CREATED)
def create_warehouse(
    body: CreateWarehouseRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(ECA_ADMIN)),
):
    """A warehouse of the actor's own ECA. Everything that happens in it (inventory, weighings, sales)
    belongs to that ECA."""
    organization = db.get(Organization, actor.organization_id) if actor.organization_id else None
    if organization is None:
        raise ApiError("no_organization", status_code=status.HTTP_403_FORBIDDEN,
                       detail="Tu cuenta no está asociada a una organización")
    if organization.status != OrganizationStatus.approved:
        raise ApiError("organization_not_active", status_code=status.HTTP_403_FORBIDDEN,
                       detail="Tu organización no está activa")
    warehouse = Warehouse(name=body.name.strip(), address=body.address, organization_id=organization.id)
    db.add(warehouse)
    db.flush()
    audit.record(db, Action.WAREHOUSE_CREATED, actor=actor, target_type="warehouse", target_id=warehouse.id)
    db.commit()
    db.refresh(warehouse)
    return warehouse


@router.get("/materials", response_model=list[MaterialResponse])
def list_materials(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(*INVENTORY_READ)),
):
    return db.scalars(select(Material).where(Material.is_active.is_(True)).order_by(Material.label)).all()


@router.get("/{item_id}", response_model=InventoryItemResponse)
def get_inventory_item(
    item_id: uuid.UUID,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*INVENTORY_READ)),
):
    item = db.scalars(select(InventoryItem).where(
        InventoryItem.id == item_id, scope.inventory_scope(actor, InventoryItem.warehouse_id))).first()
    if not item:
        raise ApiError("inventory_item_not_found", status_code=status.HTTP_404_NOT_FOUND, detail="Ítem no encontrado")
    return item


@router.patch("/{item_id}", response_model=InventoryItemResponse)
def update_inventory_item(
    item_id: uuid.UUID,
    request: UpdateInventoryItemRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*INVENTORY_WRITE)),
):
    item = db.scalars(select(InventoryItem).where(
        InventoryItem.id == item_id, scope.inventory_write_scope(actor, InventoryItem.warehouse_id))).first()
    if not item:
        raise ApiError("inventory_item_not_found", status_code=status.HTTP_404_NOT_FOUND, detail="Ítem no encontrado")

    changes: dict[str, list[str]] = {}
    if request.stock_min_kg is not None:
        changes["stock_min_kg"] = [str(item.stock_min_kg), str(request.stock_min_kg)]
        item.stock_min_kg = request.stock_min_kg
    if request.price_per_kg is not None:
        changes["price_per_kg"] = [str(item.price_per_kg), str(request.price_per_kg)]
        item.price_per_kg = request.price_per_kg

    if changes:
        audit.record(db, Action.INVENTORY_UPDATED, actor=actor, target_type="inventory_item",
                     target_id=item.id, details={"material": item.material_code, "changes": changes})
    db.commit()
    db.refresh(item)
    return item
