"""Review of applications to join, from the backoffice: the queue, taking one, and deciding.

Deciding is one unit of work: the status, the review record, the audit entry and (when approving) the first
administrator's account are committed together. The emails are sent by the router afterwards.
"""
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import search
from app.core.errors import ApiError
from app.core.pagination import paginate
from app.domains.admin import documents_service
from app.domains.applications import service as applications
from app.domains.applications.models import OrganizationApplication, OrganizationReview
from app.domains.audit import service as audit
from app.domains.audit.actions import Action
from app.domains.auth import service as auth_service
from app.domains.organizations.enums import OrganizationStatus, OrganizationType
from app.domains.organizations.models import Organization
from app.domains.users.models import User

QUEUE = (OrganizationStatus.submitted, OrganizationStatus.in_review, OrganizationStatus.changes_requested)
REVIEWABLE = (OrganizationStatus.submitted, OrganizationStatus.in_review)
ADMIN_ROLE = {OrganizationType.eca: "eca_admin", OrganizationType.association: "association_admin"}


@dataclass
class Outcome:
    """What the router needs to send the right email after a decision."""

    decision: str
    application: OrganizationApplication
    organization: Organization
    summary: str | None
    token: str | None = None  # a fresh link (changes requested) or the activation token (approved)
    admin: User | None = None
    documents: list[dict] = field(default_factory=list)  # what was sent back (changes requested)


def load(db: Session, organization_id: uuid.UUID, lock: bool = False) -> tuple[OrganizationApplication, Organization]:
    query = (select(OrganizationApplication, Organization)
             .join(Organization, Organization.id == OrganizationApplication.organization_id)
             .where(Organization.id == organization_id))
    if lock:
        query = query.with_for_update()
    row = db.execute(query).first()
    if row is None:
        raise ApiError("application_not_found", status_code=status.HTTP_404_NOT_FOUND, detail="Solicitud no encontrada")
    return row[0], row[1]


def _reviewers(db: Session, ids: set[uuid.UUID]) -> dict[uuid.UUID, dict]:
    if not ids:
        return {}
    return {u.id: {"id": u.id, "full_name": u.full_name}
            for u in db.scalars(select(User).where(User.id.in_(ids))).all()}


def _summary(organization: Organization, application: OrganizationApplication, reviewers: dict) -> dict:
    return {
        "id": organization.id, "type": organization.type, "status": organization.status,
        "legal_name": organization.legal_name, "tax_id": organization.tax_id, "city": organization.city,
        "applicant_name": application.applicant_name, "applicant_email": application.applicant_email,
        "submitted_at": application.submitted_at, "submission_count": application.submission_count,
        "review_started_at": application.review_started_at,
        "reviewer": reviewers.get(application.reviewer_id) if application.reviewer_id else None,
    }


def list_applications(
    db: Session, *, q: str | None, status_: OrganizationStatus | None, type_: OrganizationType | None,
    limit: int, offset: int,
) -> tuple[int, list[dict]]:
    """The queue: by default what is waiting or being reviewed, the oldest sent first."""
    query = (select(Organization)
             .join(OrganizationApplication, OrganizationApplication.organization_id == Organization.id)
             .where(Organization.status == status_ if status_ else Organization.status.in_(QUEUE)))
    if type_:
        query = query.where(Organization.type == type_)
    text_match = search.contains(q, [
        Organization.legal_name, Organization.tax_id, OrganizationApplication.applicant_name,
        OrganizationApplication.applicant_email])
    if text_match is not None:
        query = query.where(text_match)
    total, orgs = paginate(
        db, query, OrganizationApplication.submitted_at.asc().nulls_last(), Organization.id,
        limit=limit, offset=offset)
    if not orgs:
        return total, []
    apps = {a.organization_id: a for a in db.scalars(select(OrganizationApplication).where(
        OrganizationApplication.organization_id.in_([o.id for o in orgs]))).all()}
    reviewers = _reviewers(db, {a.reviewer_id for a in apps.values() if a.reviewer_id})
    return total, [_summary(o, apps[o.id], reviewers) for o in orgs]


def get_detail(db: Session, actor: User, organization_id: uuid.UUID, audited: bool = True) -> dict:
    """The whole request with the applicant's data and the history of reviews. Opening it is audited."""
    application, organization = load(db, organization_id)
    if audited:
        audit.record(db, Action.ADMIN_APPLICATION_VIEWED, actor=actor, target_type="organization",
                     target_id=organization.id)
        db.commit()
    reviews = db.scalars(select(OrganizationReview).where(OrganizationReview.organization_id == organization.id)
                         .order_by(OrganizationReview.submission_number, OrganizationReview.created_at, OrganizationReview.id)).all()
    wanted = {r.reviewer_id for r in reviews}
    if application.reviewer_id is not None:
        wanted.add(application.reviewer_id)
    reviewers = _reviewers(db, wanted)
    return {
        **_summary(organization, application, reviewers),
        "legal_representative": organization.legal_representative, "contact_email": organization.contact_email,
        "contact_phone": organization.contact_phone, "address": organization.address,
        "applicant_id_type": application.applicant_id_type, "applicant_id_number": application.applicant_id_number,
        "applicant_phone": application.applicant_phone, "email_verified_at": application.email_verified_at,
        "consent_at": application.consent_at, "consent_version": application.consent_version,
        "documents": documents_service.slots_for_review(db, organization),
        "reviews": [{
            "id": r.id, "decision": r.decision, "summary": r.summary, "submission_number": r.submission_number,
            "details": r.details or [], "created_at": r.created_at, "reviewer": reviewers.get(r.reviewer_id)}
            for r in reviews],
    }


def start_review(db: Session, actor: User, organization_id: uuid.UUID) -> dict:
    """Take a sent request: submitted -> in_review, assigned to the reviewer. Taking your own again is a no-op."""
    application, organization = load(db, organization_id, lock=True)
    if organization.status == OrganizationStatus.in_review:
        if application.reviewer_id == actor.id:
            return get_detail(db, actor, organization_id, audited=False)
        raise ApiError("already_in_review", status_code=status.HTTP_409_CONFLICT,
                       detail="Otra persona de Somos R ya está revisando esta solicitud")
    if organization.status != OrganizationStatus.submitted:
        raise ApiError("application_not_reviewable", status_code=status.HTTP_409_CONFLICT,
                       detail="La solicitud no está esperando revisión")
    organization.status = OrganizationStatus.in_review
    application.reviewer_id = actor.id
    application.review_started_at = datetime.now(timezone.utc)
    audit.record(db, Action.APPLICATION_REVIEW_STARTED, actor=actor, target_type="organization",
                 target_id=organization.id, details={"submission": application.submission_count})
    db.commit()
    return get_detail(db, actor, organization_id, audited=False)


def decide(db: Session, actor: User, organization_id: uuid.UUID, decision: str, summary: str | None) -> Outcome:
    """approve | request_changes | reject. Any person of Somos R may decide a sent request."""
    application, organization = load(db, organization_id, lock=True)
    if organization.status not in REVIEWABLE:
        raise ApiError("application_not_reviewable", status_code=status.HTTP_409_CONFLICT,
                       detail="La solicitud no está esperando revisión")
    summary = summary.strip() if summary else None
    if decision in ("request_changes", "reject") and not summary:
        raise ApiError("summary_required", status_code=422, detail="Indica el motivo para el solicitante")

    outcome = Outcome(decision, application, organization, summary)
    review_details: list[dict] | None = None
    if decision == "approve":
        blocked = documents_service.required_not_approved(db, organization)
        if blocked:
            raise ApiError("documents_not_approved", status_code=status.HTTP_409_CONFLICT,
                           detail=f"Faltan documentos obligatorios aprobados: {', '.join(blocked)}")
        outcome.admin, outcome.token = _approve(db, application, organization)
        recorded, action = "approved", Action.APPLICATION_APPROVED
    elif decision == "request_changes":
        organization.status = OrganizationStatus.changes_requested
        outcome.token = applications.issue_link(application)  # a fresh link comes in the email
        outcome.documents = review_details = documents_service.snapshot_for_changes(db, organization)
        recorded, action = "changes_requested", Action.APPLICATION_CHANGES_REQUESTED
    else:
        organization.status = OrganizationStatus.rejected
        application.access_token_hash = None
        application.access_token_expires_at = None
        recorded, action = "rejected", Action.APPLICATION_REJECTED

    application.reviewer_id = actor.id
    db.add(OrganizationReview(organization_id=organization.id, reviewer_id=actor.id, decision=recorded,
                              summary=summary, submission_number=application.submission_count,
                              details=review_details))
    # The reason is free text and may name people: the audit keeps only that it exists.
    details: dict = {"submission": application.submission_count, "has_summary": bool(summary)}
    if outcome.admin is not None:
        details["admin_user_id"] = str(outcome.admin.id)
    audit.record(db, action, actor=actor, target_type="organization", target_id=organization.id, details=details)
    db.commit()
    return outcome


def _approve(db: Session, application: OrganizationApplication, organization: Organization) -> tuple[User, str]:
    """Approve and create the first administrator: the applicant, who sets their own password by a one-use link."""
    applications.ensure_complete(db, application, organization)
    applications.ensure_not_operating(db, organization, organization.tax_id)
    taken = db.scalar(select(User.id).where(
        (User.email == application.applicant_email) | (User.id_number == application.applicant_id_number)))
    if taken is not None:
        raise ApiError("applicant_account_conflict", status_code=status.HTTP_409_CONFLICT,
                       detail="El correo o el documento de quien aplica ya pertenece a otra cuenta")
    user = User(
        email=application.applicant_email, full_name=application.applicant_name, phone=application.applicant_phone,
        id_type=application.applicant_id_type, id_number=application.applicant_id_number,
        user_type_code=organization.type.value, role_code=ADMIN_ROLE[organization.type],
        organization_id=organization.id, password_hash="", email_verified_at=application.email_verified_at,
        # Legacy per-person copies of the organization's data (the organization is the source of truth).
        association_nit=organization.tax_id if organization.type == OrganizationType.association else None,
        legal_representative=(organization.legal_representative
                              if organization.type == OrganizationType.association else None),
    )
    try:
        db.add(user)
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ApiError("applicant_account_conflict", status_code=status.HTTP_409_CONFLICT,
                       detail="El correo o el documento de quien aplica ya pertenece a otra cuenta")
    token = auth_service.issue_token(db, user, auth_service.ACTIVATE)
    organization.status = OrganizationStatus.approved
    organization.approved_at = datetime.now(timezone.utc)
    application.access_token_hash = None  # the applicant now has an account: the magic link is over
    application.access_token_expires_at = None
    return user, token
