"""The public request of an Association or an ECA to join Somos R.

The applicant has no account: they come back to their request through a magic link (a random token whose
SHA-256 is all that is stored). Using the link also proves they own the email, which is required before a
reviewer looks at the request. Every function that changes data commits and audits.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.email import send_email
from app.core.errors import ApiError
from app.domains.applications.models import OrganizationApplication, OrganizationReview
from app.domains.applications.schemas import (
    StartApplicationRequest,
    UpdateApplicationRequest,
)
from app.domains.audit import service as audit
from app.domains.audit.actions import Action
from app.domains.catalogs.service import ensure_document_type_is_active
from app.domains.organizations.enums import OrganizationStatus
from app.domains.organizations.models import Organization

# The applicant may edit and resend only in these states.
EDITABLE = (OrganizationStatus.draft, OrganizationStatus.changes_requested)
# Requests that are still alive: the applicant can come back to them.
OPEN = (OrganizationStatus.draft, OrganizationStatus.submitted, OrganizationStatus.in_review,
        OrganizationStatus.changes_requested)
# What a request must have before it can be sent.
REQUIRED_TO_SUBMIT = ("legal_name", "tax_id", "legal_representative", "contact_email", "contact_phone",
                      "address", "city")
# The person who applies becomes the first administrator when approved: their account needs these.
APPLICANT_REQUIRED = ("applicant_id_type", "applicant_id_number", "applicant_phone")
APPLICANT_FIELDS = ("applicant_name",) + APPLICANT_REQUIRED


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _invalid_link() -> ApiError:
    return ApiError("invalid_application_link", status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="El enlace de la solicitud no es válido o venció")


def _clean(value: str | None) -> str | None:
    value = value.strip() if value is not None else None
    return value or None


def issue_link(application: OrganizationApplication) -> str:
    """A new magic link; the previous one stops working. Caller commits."""
    raw = secrets.token_urlsafe(32)
    application.access_token_hash = _hash(raw)
    application.access_token_expires_at = datetime.now(timezone.utc) + timedelta(days=settings.application_link_days)
    return raw


def ensure_not_operating(db: Session, organization: Organization, tax_id: str | None) -> None:
    """A tax id already held by an organization that operates cannot be requested again."""
    if tax_id is None:
        return
    taken = db.scalar(select(Organization.id).where(
        Organization.type == organization.type, Organization.tax_id == tax_id, Organization.id != organization.id,
        Organization.status.in_((OrganizationStatus.approved, OrganizationStatus.suspended))))
    if taken is not None:
        raise ApiError("organization_already_registered", status_code=status.HTTP_409_CONFLICT,
                       detail="Ya existe una organización registrada con ese NIT")


# --- Starting and coming back --------------------------------------------------------------

def start(db: Session, request: StartApplicationRequest) -> tuple[str, OrganizationApplication, Organization]:
    """Open an application. If this email already has one for the same type, it is not duplicated: the
    applicant just gets a fresh link. Returns the raw link token to email."""
    email = str(request.applicant_email).lower()
    existing = db.execute(
        select(OrganizationApplication, Organization)
        .join(Organization, Organization.id == OrganizationApplication.organization_id)
        .where(OrganizationApplication.applicant_email == email, Organization.type == request.type,
               Organization.status.in_(OPEN))
        .order_by(OrganizationApplication.created_at)).first()
    if existing is not None:
        application, organization = existing
        token = issue_link(application)
        audit.record(db, Action.APPLICATION_LINK_SENT, target_type="organization", target_id=organization.id)
        db.commit()
        return token, application, organization

    tax_id = _clean(request.tax_id)
    organization = Organization(type=request.type, status=OrganizationStatus.draft,
                                legal_name=request.legal_name.strip(), tax_id=tax_id)
    ensure_not_operating(db, organization, tax_id)
    db.add(organization)
    db.flush()
    application = OrganizationApplication(
        organization_id=organization.id, applicant_name=request.applicant_name.strip(), applicant_email=email,
        consent_version=settings.application_consent_version, consent_at=datetime.now(timezone.utc))
    token = issue_link(application)
    db.add(application)
    audit.record(db, Action.APPLICATION_CREATED, target_type="organization", target_id=organization.id,
                 details={"type": request.type.value, "consent_version": application.consent_version})
    db.commit()
    return token, application, organization


def resend_links(db: Session, email: str) -> list[tuple[str, OrganizationApplication, Organization]]:
    """A fresh link for each open application of this email (usually one). The caller answers the same
    whether or not there were any, so the endpoint does not reveal who has applied."""
    rows = db.execute(
        select(OrganizationApplication, Organization)
        .join(Organization, Organization.id == OrganizationApplication.organization_id)
        .where(OrganizationApplication.applicant_email == email.lower(), Organization.status.in_(OPEN))
        .order_by(OrganizationApplication.created_at).limit(3)).all()
    issued = []
    for application, organization in rows:
        issued.append((issue_link(application), application, organization))
        audit.record(db, Action.APPLICATION_LINK_SENT, target_type="organization", target_id=organization.id)
    if issued:
        db.commit()
    return issued


def authenticate(db: Session, raw_token: str | None) -> tuple[OrganizationApplication, Organization]:
    """The application behind a magic link. Any problem answers the same 401. The first use verifies the email."""
    if not raw_token:
        raise _invalid_link()
    application = db.scalars(select(OrganizationApplication).where(
        OrganizationApplication.access_token_hash == _hash(raw_token))).first()
    now = datetime.now(timezone.utc)
    if application is None or application.access_token_expires_at is None or application.access_token_expires_at <= now:
        raise _invalid_link()
    organization = db.get(Organization, application.organization_id)
    if organization is None or organization.status not in OPEN:
        raise _invalid_link()  # approved (now has an account) or rejected: the link is over
    if application.email_verified_at is None:
        application.email_verified_at = now
        db.commit()
    return application, organization


# --- The applicant's own request ------------------------------------------------------------

def missing_fields(application: OrganizationApplication, organization: Organization) -> list[str]:
    return ([name for name in REQUIRED_TO_SUBMIT if not getattr(organization, name)]
            + [name for name in APPLICANT_REQUIRED if not getattr(application, name)])


def latest_feedback(db: Session, organization: Organization) -> dict | None:
    """What the reviewer asked to correct, while the applicant has the request back in their hands."""
    if organization.status != OrganizationStatus.changes_requested:
        return None
    review = db.scalars(select(OrganizationReview).where(
        OrganizationReview.organization_id == organization.id, OrganizationReview.decision == "changes_requested",
    ).order_by(OrganizationReview.submission_number.desc(), OrganizationReview.created_at.desc())).first()
    if review is None:
        return None
    return {"summary": review.summary, "created_at": review.created_at, "submission_number": review.submission_number}


def view(db: Session, application: OrganizationApplication, organization: Organization) -> dict:
    missing = missing_fields(application, organization)
    left = max(settings.application_max_submissions - application.submission_count, 0)
    can_edit = organization.status in EDITABLE
    return {
        "id": organization.id, "type": organization.type, "status": organization.status,
        "legal_name": organization.legal_name, "tax_id": organization.tax_id,
        "legal_representative": organization.legal_representative, "contact_email": organization.contact_email,
        "contact_phone": organization.contact_phone, "address": organization.address, "city": organization.city,
        "applicant_name": application.applicant_name, "applicant_email": application.applicant_email,
        "applicant_id_type": application.applicant_id_type, "applicant_id_number": application.applicant_id_number,
        "applicant_phone": application.applicant_phone, "feedback": latest_feedback(db, organization),
        "consent_at": application.consent_at, "submitted_at": application.submitted_at,
        "submission_count": application.submission_count, "submissions_left": left,
        "can_edit": can_edit, "can_submit": can_edit and not missing and left > 0, "missing_fields": missing,
    }


def _ensure_editable(organization: Organization) -> None:
    if organization.status not in EDITABLE:
        raise ApiError("application_locked", status_code=status.HTTP_409_CONFLICT,
                       detail="La solicitud ya fue enviada: no se puede editar hasta que se pidan correcciones")


def update(db: Session, application: OrganizationApplication, organization: Organization,
           request: UpdateApplicationRequest) -> None:
    _ensure_editable(organization)
    fields = request.model_dump(exclude_unset=True)
    if fields.get("applicant_id_type") is not None:
        ensure_document_type_is_active(db, fields["applicant_id_type"])
    if "tax_id" in fields:
        fields["tax_id"] = _clean(fields["tax_id"])
        ensure_not_operating(db, organization, fields["tax_id"])
    changed = []
    for name, value in fields.items():
        value = _clean(str(value)) if value is not None else None
        if name in APPLICANT_FIELDS:
            if name == "applicant_name" and value is None:
                continue  # the name cannot be emptied
            if getattr(application, name) != value:
                setattr(application, name, value)
                changed.append(name)
            continue
        if name == "legal_name" and value is None:
            continue  # the name cannot be emptied
        if getattr(organization, name) != value:
            setattr(organization, name, value)
            changed.append(name)
    if changed:
        audit.record(db, Action.APPLICATION_UPDATED, target_type="organization", target_id=organization.id,
                     details={"fields": changed})
    db.commit()


def ensure_complete(application: OrganizationApplication, organization: Organization) -> None:
    missing = missing_fields(application, organization)
    if missing:
        raise ApiError("application_incomplete", status_code=422,
                       detail=f"Faltan datos para enviar la solicitud: {', '.join(missing)}")


def submit(db: Session, application: OrganizationApplication, organization: Organization) -> None:
    _ensure_editable(organization)
    if application.submission_count >= settings.application_max_submissions:
        raise ApiError("too_many_submissions", status_code=status.HTTP_409_CONFLICT,
                       detail="Se alcanzó el máximo de envíos de esta solicitud")
    ensure_complete(application, organization)
    ensure_not_operating(db, organization, organization.tax_id)
    organization.status = OrganizationStatus.submitted
    application.submitted_at = datetime.now(timezone.utc)
    application.submission_count += 1
    audit.record(db, Action.APPLICATION_SUBMITTED, target_type="organization", target_id=organization.id,
                 details={"submission": application.submission_count})
    db.commit()


# --- Emails ---------------------------------------------------------------------------------

def _link(token: str) -> str:
    return f"{settings.frontend_url.rstrip('/')}/solicitud?token={token}"


def send_access_link_email(to: str, applicant_name: str, organization_name: str, token: str) -> None:
    send_email(
        to,
        "Continúa tu solicitud en Somos R",
        f"Hola {applicant_name},\n\n"
        f"Este es el enlace para ver, completar y enviar la solicitud de {organization_name}:\n"
        f"{_link(token)}\n\n"
        f"Es personal: no lo compartas. Funciona durante {settings.application_link_days} días; si pides otro, este deja de servir.\n"
        "Si no empezaste esta solicitud, ignora este mensaje.",
    )


def send_submitted_email(to: str, applicant_name: str, organization_name: str) -> None:
    send_email(
        to,
        "Recibimos tu solicitud en Somos R",
        f"Hola {applicant_name},\n\n"
        f"Recibimos la solicitud de {organization_name}. El equipo de Somos R la revisará y te escribiremos a este "
        "correo con la decisión o con lo que haya que corregir.",
    )


def send_changes_requested_email(to: str, applicant_name: str, organization_name: str, summary: str, token: str) -> None:
    send_email(
        to,
        "Tu solicitud en Somos R necesita correcciones",
        f"Hola {applicant_name},\n\n"
        f"Revisamos la solicitud de {organization_name} y necesita estos cambios:\n\n{summary}\n\n"
        f"Corrígela y envíala de nuevo desde este enlace (el anterior dejó de servir):\n{_link(token)}",
    )


def send_rejected_email(to: str, applicant_name: str, organization_name: str, summary: str) -> None:
    send_email(
        to,
        "Respuesta a tu solicitud en Somos R",
        f"Hola {applicant_name},\n\n"
        f"Revisamos la solicitud de {organization_name} y no pudimos aprobarla:\n\n{summary}\n\n"
        "Si crees que hay un error, escríbenos.",
    )


def send_approved_email(to: str, applicant_name: str, organization_name: str, activation_link: str) -> None:
    send_email(
        to,
        "Tu organización fue aprobada en Somos R",
        f"Hola {applicant_name},\n\n"
        f"La solicitud de {organization_name} fue aprobada y tu cuenta de administrador ya existe. "
        f"Crea tu contraseña para entrar:\n{activation_link}\n\n"
        f"El enlace es de un solo uso y vence en {settings.activation_token_minutes // 60} horas.",
    )
