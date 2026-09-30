"""The directory of Associations and the links between ECAs and Associations.

The ECA asks, the Association's admin accepts or rejects, and either side may remove the link later. A
link only concerns its two organizations: anyone else answers 404, like a link that does not exist.
Each function that changes data is a unit of work: it commits and audits.
"""
import uuid
from datetime import datetime, timezone

from fastapi import status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core import search
from app.core.errors import ApiError
from app.core.pagination import paginate
from app.domains.audit import service as audit
from app.domains.audit.actions import Action
from app.domains.organizations.enums import (
    LinkStatus,
    OrganizationStatus,
    OrganizationType,
)
from app.domains.organizations.models import EcaAssociationLink, Organization
from app.domains.users.models import User


def _own_organization(db: Session, actor: User, expected: OrganizationType | None = None) -> Organization:
    """The actor's organization, approved and (optionally) of the expected type. Fails closed."""
    org = db.get(Organization, actor.organization_id) if actor.organization_id else None
    if org is None:
        raise ApiError("no_organization", status_code=status.HTTP_403_FORBIDDEN,
                       detail="Tu cuenta no está asociada a una organización")
    if org.status != OrganizationStatus.approved:
        raise ApiError("organization_not_active", status_code=status.HTTP_403_FORBIDDEN,
                       detail="Tu organización no está activa")
    if expected is not None and org.type != expected:
        raise ApiError("forbidden", status_code=status.HTTP_403_FORBIDDEN,
                       detail="No tienes permisos para realizar esta acción")
    return org


def _link_not_found() -> ApiError:
    return ApiError("link_not_found", status_code=status.HTTP_404_NOT_FOUND, detail="Vínculo no encontrado")


def _get_link(db: Session, link_id: uuid.UUID, org: Organization) -> EcaAssociationLink:
    """A link the organization is a party of; anything else looks like a missing link."""
    link = db.get(EcaAssociationLink, link_id)
    if link is None or org.id not in (link.eca_id, link.association_id):
        raise _link_not_found()
    return link


# --- Directory ----------------------------------------------------------------------------

def directory(db: Session, actor: User, q: str | None, limit: int, offset: int) -> tuple[int, list[dict]]:
    """Approved Associations an ECA may ask to link with, each with the state of its link (if any)."""
    eca = _own_organization(db, actor, OrganizationType.eca)
    query = select(Organization).where(
        Organization.type == OrganizationType.association, Organization.status == OrganizationStatus.approved)
    name_match = search.contains(q, [Organization.legal_name])
    if name_match is not None:
        query = query.where(name_match)
    total, orgs = paginate(db, query, Organization.legal_name, Organization.id, limit=limit, offset=offset)
    links = {
        link.association_id: link.status
        for link in db.scalars(select(EcaAssociationLink).where(
            EcaAssociationLink.eca_id == eca.id, EcaAssociationLink.association_id.in_([o.id for o in orgs])))
    }
    return total, [
        {"id": o.id, "legal_name": o.legal_name, "city": o.city, "link_status": links.get(o.id)} for o in orgs]


# --- Links --------------------------------------------------------------------------------

def request_link(db: Session, actor: User, association_id: uuid.UUID) -> EcaAssociationLink:
    eca = _own_organization(db, actor, OrganizationType.eca)
    association = db.get(Organization, association_id)
    if association is None or association.type != OrganizationType.association:
        raise ApiError("organization_not_found", status_code=status.HTTP_404_NOT_FOUND,
                       detail="Asociación no encontrada")
    if association.status != OrganizationStatus.approved:
        raise ApiError("organization_not_active", status_code=status.HTTP_409_CONFLICT,
                       detail="La asociación no está activa")

    link = db.scalars(select(EcaAssociationLink).where(
        EcaAssociationLink.eca_id == eca.id, EcaAssociationLink.association_id == association.id)).first()
    if link is not None and link.status == LinkStatus.requested:
        raise ApiError("link_already_requested", status_code=status.HTTP_409_CONFLICT,
                       detail="Ya solicitaste este vínculo y está esperando respuesta")
    if link is not None and link.status == LinkStatus.active:
        raise ApiError("link_already_active", status_code=status.HTTP_409_CONFLICT,
                       detail="Ya estás vinculada a esta asociación")
    if link is None:
        link = EcaAssociationLink(eca_id=eca.id, association_id=association.id)
        db.add(link)
    # A new question after a rejection or a removal starts the decision over.
    link.status = LinkStatus.requested
    link.requested_by = actor.id
    link.decided_by = None
    link.decided_at = None
    link.rejection_reason = None
    db.flush()
    audit.record(db, Action.LINK_REQUESTED, actor=actor, target_type="link", target_id=link.id,
                 details={"eca_id": str(eca.id), "association_id": str(association.id)})
    db.commit()
    return _reload(db, link.id)


def list_links(db: Session, actor: User, status_filter: LinkStatus | None, limit: int, offset: int) -> tuple[int, list]:
    org = _own_organization(db, actor)
    query = select(EcaAssociationLink).where(
        EcaAssociationLink.eca_id == org.id if org.type == OrganizationType.eca
        else EcaAssociationLink.association_id == org.id)
    if status_filter is not None:
        query = query.where(EcaAssociationLink.status == status_filter)
    return paginate(
        db, query, EcaAssociationLink.updated_at.desc(), EcaAssociationLink.id, limit=limit, offset=offset,
        options=(selectinload(EcaAssociationLink.eca), selectinload(EcaAssociationLink.association)))


def decide(db: Session, actor: User, link_id: uuid.UUID, accept: bool, reason: str | None) -> EcaAssociationLink:
    """The Association's admin accepts or rejects a pending request."""
    association = _own_organization(db, actor, OrganizationType.association)
    link = _get_link(db, link_id, association)
    if link.association_id != association.id:
        raise _link_not_found()
    if link.status != LinkStatus.requested:
        raise ApiError("link_not_pending", status_code=status.HTTP_409_CONFLICT,
                       detail="Este vínculo no está esperando respuesta")
    if accept:
        eca = db.get(Organization, link.eca_id)
        if eca is None or eca.status != OrganizationStatus.approved:
            raise ApiError("organization_not_active", status_code=status.HTTP_409_CONFLICT,
                           detail="La ECA no está activa")
    link.status = LinkStatus.active if accept else LinkStatus.rejected
    link.decided_by = actor.id
    link.decided_at = datetime.now(timezone.utc)
    link.rejection_reason = None if accept else (reason or None)
    audit.record(db, Action.LINK_ACCEPTED if accept else Action.LINK_REJECTED, actor=actor,
                 target_type="link", target_id=link.id,
                 details={"eca_id": str(link.eca_id), "has_reason": bool(reason)} if not accept
                 else {"eca_id": str(link.eca_id)})
    db.commit()
    return _reload(db, link.id)


def remove(db: Session, actor: User, link_id: uuid.UUID) -> EcaAssociationLink:
    """Either side ends an active link; the ECA can also withdraw a request that has not been answered."""
    org = _own_organization(db, actor)
    link = _get_link(db, link_id, org)
    is_eca = link.eca_id == org.id
    removable = link.status == LinkStatus.active or (link.status == LinkStatus.requested and is_eca)
    if not removable:
        raise ApiError("link_not_removable", status_code=status.HTTP_409_CONFLICT,
                       detail="Este vínculo no se puede retirar en su estado actual")
    previous = link.status.value
    link.status = LinkStatus.removed
    link.decided_by = actor.id
    link.decided_at = datetime.now(timezone.utc)
    audit.record(db, Action.LINK_REMOVED, actor=actor, target_type="link", target_id=link.id,
                 details={"from": previous, "by": "eca" if is_eca else "association"})
    db.commit()
    return _reload(db, link.id)


def _reload(db: Session, link_id: uuid.UUID) -> EcaAssociationLink:
    return db.scalars(select(EcaAssociationLink).where(EcaAssociationLink.id == link_id).options(
        selectinload(EcaAssociationLink.eca), selectinload(EcaAssociationLink.association))).one()

