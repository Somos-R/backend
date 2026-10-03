import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.network import enforce_admin_network
from app.core.rate_limit import admin_limit, limiter
from app.core.security import require_capability
from app.domains.admin import applications_service as service
from app.domains.applications import service as applications
from app.domains.auth import service as auth_service
from app.domains.organizations.enums import OrganizationStatus, OrganizationType
from app.domains.users.models import User

# The review of applications: needs `organizations.review`, a backoffice token and an allowed network.
router = APIRouter(prefix="/admin/applications", tags=["admin"], dependencies=[Depends(enforce_admin_network)])
Reviewer = Depends(require_capability("organizations.review"))


class ReviewerRef(BaseModel):
    id: uuid.UUID
    full_name: str


class ApplicationSummary(BaseModel):
    id: uuid.UUID  # the organization's id: it is how an application is addressed
    type: OrganizationType
    status: OrganizationStatus
    legal_name: str
    tax_id: str | None
    city: str | None
    applicant_name: str
    applicant_email: str
    submitted_at: datetime | None
    submission_count: int
    review_started_at: datetime | None
    reviewer: ReviewerRef | None


class ApplicationListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[ApplicationSummary]


class ReviewEntry(BaseModel):
    id: uuid.UUID
    decision: Literal["approved", "changes_requested", "rejected"]
    summary: str | None
    submission_number: int
    created_at: datetime
    reviewer: ReviewerRef | None


class ApplicationDetail(ApplicationSummary):
    legal_representative: str | None
    contact_email: str | None
    contact_phone: str | None
    address: str | None
    applicant_id_type: str | None
    applicant_id_number: str | None
    applicant_phone: str | None
    email_verified_at: datetime | None
    consent_at: datetime
    consent_version: str
    reviews: list[ReviewEntry]


class DecisionRequest(BaseModel):
    decision: Literal["approve", "request_changes", "reject"]
    summary: str | None = Field(default=None, max_length=2000, description="Obligatorio al pedir correcciones o rechazar: es lo que lee el solicitante")

    @model_validator(mode="after")
    def _summary_when_needed(self):
        if self.decision != "approve" and not (self.summary and len(self.summary.strip()) >= 10):
            raise ValueError("Indica el motivo para el solicitante (mínimo 10 caracteres)")
        return self


@router.get("", response_model=ApplicationListResponse)
@limiter.limit(admin_limit)
def list_applications(
    request: Request,
    q: str | None = Query(default=None, max_length=100, description="Organización, NIT, nombre o correo de quien aplica"),
    status: OrganizationStatus | None = Query(default=None, description="Por defecto: enviadas, en revisión y con correcciones"),
    type: OrganizationType | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _: User = Reviewer,
):
    """La cola de solicitudes: las más antiguas enviadas primero."""
    total, items = service.list_applications(db, q=q, status_=status, type_=type, limit=limit, offset=offset)
    return ApplicationListResponse(total=total, limit=limit, offset=offset, items=items)


@router.get("/{organization_id}", response_model=ApplicationDetail)
@limiter.limit(admin_limit)
def get_application(request: Request, organization_id: uuid.UUID, db: Session = Depends(get_db), actor: User = Reviewer):
    """La solicitud completa con los datos de quien aplica y el historial de revisiones. Abrirla queda auditado."""
    return service.get_detail(db, actor, organization_id)


@router.post("/{organization_id}/start-review", response_model=ApplicationDetail)
@limiter.limit(admin_limit)
def start_review(request: Request, organization_id: uuid.UUID, db: Session = Depends(get_db), actor: User = Reviewer):
    """Tomar una solicitud enviada: pasa a `in_review` y queda a nombre de quien la toma."""
    return service.start_review(db, actor, organization_id)


@router.post("/{organization_id}/decision", response_model=ApplicationDetail)
@limiter.limit(admin_limit)
def decide(
    request: Request,
    organization_id: uuid.UUID,
    body: DecisionRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    actor: User = Reviewer,
):
    """Aprobar (crea al primer administrador y le envía su enlace de activación), pedir correcciones o rechazar."""
    outcome = service.decide(db, actor, organization_id, body.decision, body.summary)
    application, organization = outcome.application, outcome.organization
    if outcome.decision == "approve" and outcome.admin and outcome.token:
        background_tasks.add_task(
            applications.send_approved_email, application.applicant_email, application.applicant_name,
            organization.legal_name, auth_service.activation_link(outcome.token))
    elif outcome.decision == "request_changes" and outcome.token and outcome.summary:
        background_tasks.add_task(
            applications.send_changes_requested_email, application.applicant_email, application.applicant_name,
            organization.legal_name, outcome.summary, outcome.token)
    elif outcome.decision == "reject" and outcome.summary:
        background_tasks.add_task(
            applications.send_rejected_email, application.applicant_email, application.applicant_name,
            organization.legal_name, outcome.summary)
    return service.get_detail(db, actor, organization_id, audited=False)  # deciding is audited as such, not as a view
