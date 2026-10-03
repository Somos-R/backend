"""Organizations from the backoffice: find them and see who belongs to them and who they are linked with.

Read-only. Changing an organization's status belongs to the onboarding review (applications), not here.
"""
import uuid

from fastapi import status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core import search
from app.core.errors import ApiError
from app.core.pagination import paginate
from app.domains.admin import users_service
from app.domains.audit import service as audit
from app.domains.audit.actions import Action
from app.domains.inventory.models import Warehouse
from app.domains.organizations.enums import (
    LinkStatus,
    OrganizationStatus,
    OrganizationType,
)
from app.domains.organizations.models import EcaAssociationLink, Organization
from app.domains.users.models import User

_STAFF_TYPES = ("eca", "association")


def _counts(db: Session, ids: list[uuid.UUID]) -> tuple[dict[uuid.UUID, int], dict[uuid.UUID, int]]:
    """Staff and active links per organization for one page, in two grouped queries."""
    staff: dict[uuid.UUID, int] = {
        org_id: count for org_id, count in db.execute(
            select(User.organization_id, func.count(User.id))
            .where(User.organization_id.in_(ids), User.user_type_code.in_(_STAFF_TYPES))
            .group_by(User.organization_id)).all() if org_id is not None}
    links: dict[uuid.UUID, int] = {}
    for column in (EcaAssociationLink.eca_id, EcaAssociationLink.association_id):
        for org_id, count in db.execute(
                select(column, func.count(EcaAssociationLink.id))
                .where(column.in_(ids), EcaAssociationLink.status == LinkStatus.active)
                .group_by(column)).all():
            links[org_id] = links.get(org_id, 0) + count
    return staff, links


def _summary(org: Organization, staff: int, active_links: int) -> dict:
    return {
        "id": org.id, "type": org.type, "status": org.status, "legal_name": org.legal_name, "tax_id": org.tax_id,
        "city": org.city, "contact_email": org.contact_email, "created_at": org.created_at,
        "approved_at": org.approved_at, "staff_count": staff, "active_links": active_links,
    }


def list_organizations(
    db: Session, *, q: str | None, type_: OrganizationType | None, status_: OrganizationStatus | None,
    limit: int, offset: int,
) -> tuple[int, list[dict]]:
    query = select(Organization)
    text_match = search.contains(q, [
        Organization.legal_name, Organization.tax_id, Organization.contact_email, Organization.legal_representative])
    if text_match is not None:
        query = query.where(text_match)
    if type_:
        query = query.where(Organization.type == type_)
    if status_:
        query = query.where(Organization.status == status_)
    total, orgs = paginate(db, query, Organization.legal_name, Organization.id, limit=limit, offset=offset)
    if not orgs:
        return total, []
    staff, links = _counts(db, [o.id for o in orgs])
    return total, [_summary(o, staff.get(o.id, 0), links.get(o.id, 0)) for o in orgs]


def get_detail(db: Session, actor: User, organization_id: uuid.UUID) -> dict:
    """The profile with its staff and links. Opening it is audited: staff names and emails are shown."""
    org = db.get(Organization, organization_id)
    if org is None:
        raise ApiError("organization_not_found", status_code=status.HTTP_404_NOT_FOUND,
                       detail="Organización no encontrada")
    audit.record(db, Action.ADMIN_ORGANIZATION_VIEWED, actor=actor, target_type="organization", target_id=org.id)
    db.commit()

    staff = db.scalars(select(User).where(
        User.organization_id == org.id, User.user_type_code.in_(_STAFF_TYPES)).order_by(User.full_name, User.id)).all()
    detail = {
        **_summary(org, len(staff), 0),
        "legal_representative": org.legal_representative, "contact_phone": org.contact_phone,
        "address": org.address, "updated_at": org.updated_at,
        "staff": [users_service.summary(u) for u in staff],
        "links": _links(db, org),
        "recyclers_count": None, "warehouses": None,
    }
    detail["active_links"] = sum(1 for link in detail["links"] if link["status"] == LinkStatus.active)
    if org.type == OrganizationType.association:
        detail["recyclers_count"] = db.scalar(select(func.count(User.id)).where(
            User.organization_id == org.id, User.user_type_code == "recycler")) or 0
    else:
        detail["warehouses"] = [
            {"id": w.id, "name": w.name, "is_active": w.is_active}
            for w in db.scalars(select(Warehouse).where(Warehouse.organization_id == org.id)
                                .order_by(Warehouse.name, Warehouse.id)).all()]
    return detail


def _links(db: Session, org: Organization) -> list[dict]:
    """Every link of the organization with the organization on the other side."""
    rows = db.execute(
        select(EcaAssociationLink, Organization)
        .join(Organization, or_(
            (EcaAssociationLink.eca_id == org.id) & (Organization.id == EcaAssociationLink.association_id),
            (EcaAssociationLink.association_id == org.id) & (Organization.id == EcaAssociationLink.eca_id)))
        .where(or_(EcaAssociationLink.eca_id == org.id, EcaAssociationLink.association_id == org.id))
        .order_by(Organization.legal_name, EcaAssociationLink.id)).all()
    return [{
        "id": link.id, "status": link.status, "requested_at": link.created_at, "decided_at": link.decided_at,
        "rejection_reason": link.rejection_reason,
        "other": {"id": other.id, "type": other.type, "legal_name": other.legal_name, "city": other.city},
    } for link, other in rows]
