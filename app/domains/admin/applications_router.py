import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.network import enforce_admin_network
from app.core.rate_limit import admin_limit, limiter
from app.core.security import require_capability
from app.domains.admin import applications_service as service
from app.domains.admin import documents_service
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


class ReviewedDocument(BaseModel):
    id: uuid.UUID
    original_name: str
    content_type: str
    size_bytes: int
    uploaded_at: datetime
    status: Literal["pending", "ok", "missing", "not_compliant"]
    review_comment: str | None
    reviewed_at: datetime | None
    reviewed_by: ReviewerRef | None


class DocumentTypeRef(BaseModel):
    code: str
    label: str
    is_required: bool


class DocumentSlot(BaseModel):
    """A document the organization is asked for and what it uploaded (or null)."""

    document_type: DocumentTypeRef
    document: ReviewedDocument | None


class SentBackDocument(BaseModel):
    code: str
    label: str
    status: str
    comment: str | None


class ReviewEntry(BaseModel):
    id: uuid.UUID
    decision: Literal["approved", "changes_requested", "rejected"]
    summary: str | None
    details: list[SentBackDocument]  # documents sent back with this review
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
    documents: list[DocumentSlot]
    reviews: list[ReviewEntry]


class DocumentAccess(BaseModel):
    url: str  # path under the API; opens the file for a few minutes
    expires_at: datetime


class ReviewDocumentRequest(BaseModel):
    status: Literal["ok", "missing", "not_compliant"]
    comment: str | None = Field(default=None, max_length=500, description="Obligatorio si no está bien: es lo que lee el solicitante")


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
            organization.legal_name, outcome.summary, outcome.token, outcome.documents)
    elif outcome.decision == "reject" and outcome.summary:
        background_tasks.add_task(
            applications.send_rejected_email, application.applicant_email, application.applicant_name,
            organization.legal_name, outcome.summary)
    return service.get_detail(db, actor, organization_id, audited=False)  # deciding is audited as such, not as a view


@router.post("/{organization_id}/documents/{document_id}/access", response_model=DocumentAccess)
@limiter.limit(admin_limit)
def open_document(
    request: Request, organization_id: uuid.UUID, document_id: uuid.UUID, db: Session = Depends(get_db),
    actor: User = Reviewer,
):
    """Un enlace firmado, de pocos minutos, para abrir el archivo. Pedirlo queda auditado: es el acto de verlo."""
    return documents_service.access(db, actor, organization_id, document_id)


@router.patch("/{organization_id}/documents/{document_id}", response_model=DocumentSlot)
@limiter.limit(admin_limit)
def review_document(
    request: Request, organization_id: uuid.UUID, document_id: uuid.UUID, body: ReviewDocumentRequest,
    db: Session = Depends(get_db), actor: User = Reviewer,
):
    """El veredicto sobre un documento (`ok`, `missing` o `not_compliant`, estos dos con comentario)."""
    entry = documents_service.review(db, actor, organization_id, document_id, body.status, body.comment)
    _, organization = service.load(db, organization_id)
    slot = next(s for s in documents_service.slots_for_review(db, organization)
                if s["document"] and s["document"]["id"] == entry["id"])
    return slot


# Opening a file needs no Authorization header (a browser tab cannot send one): the signed link is the
# credential. It lives under /admin, so the allowed-network restriction and the limits still apply.
download_router = APIRouter(prefix="/admin/documents", tags=["admin"], dependencies=[Depends(enforce_admin_network)])


@download_router.get("/download/{token}")
@limiter.limit(admin_limit)
def download_document(request: Request, token: str, db: Session = Depends(get_db)):
    """Entrega el archivo de un enlace firmado vigente. No hay otra forma de leer un documento."""
    data, document = documents_service.fetch_for_download(db, token)
    safe_name = "".join(c for c in document.original_name if c.isalnum() or c in " ._-()") or "documento"
    return Response(content=data, media_type=document.content_type, headers={
        "Content-Disposition": f'attachment; filename="{safe_name}"',
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    })
