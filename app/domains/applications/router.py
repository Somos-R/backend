from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Header,
    Request,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from app.core import uploads
from app.core.config import settings
from app.core.database import get_db
from app.core.errors import ApiError
from app.core.rate_limit import application_uploads_limit, applications_limit, limiter
from app.domains.applications import documents, service
from app.domains.applications.docs import (
    ACCESS_LINK_DOCS,
    CURRENT_DOCS,
    DELETE_DOCUMENT_DOCS,
    DOCUMENTS_DOCS,
    START_DOCS,
    SUBMIT_DOCS,
    UPDATE_DOCS,
    UPLOAD_DOCUMENT_DOCS,
)
from app.domains.applications.models import OrganizationApplication
from app.domains.applications.schemas import (
    AccessLinkRequest,
    ApplicationMessage,
    ApplicationView,
    DocumentSlot,
    StartApplicationRequest,
    UpdateApplicationRequest,
    UploadedDocument,
)
from app.domains.organizations.models import Organization

# Public: nobody is signed in. The applicant identifies themselves with the magic link's token in the
# `X-Application-Token` header; everything about who they are comes from it, never from the request body.
router = APIRouter(prefix="/applications", tags=["applications"])

LINK_SENT = "Si el correo es válido, te enviamos un enlace para continuar tu solicitud"


def get_application(
    token: str | None = Header(default=None, alias="X-Application-Token"),
    db: Session = Depends(get_db),
) -> tuple[OrganizationApplication, Organization]:
    return service.authenticate(db, token)


@router.post("", response_model=ApplicationMessage, status_code=status.HTTP_202_ACCEPTED, **START_DOCS)
@limiter.limit(applications_limit)
def start_application(
    request: Request,
    body: StartApplicationRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    token, application, organization = service.start(db, body)
    background_tasks.add_task(
        service.send_access_link_email, application.applicant_email, application.applicant_name,
        organization.legal_name, token)
    return ApplicationMessage(message=LINK_SENT)


@router.post("/access-link", response_model=ApplicationMessage, status_code=status.HTTP_202_ACCEPTED, **ACCESS_LINK_DOCS)
@limiter.limit(applications_limit)
def request_access_link(
    request: Request,
    body: AccessLinkRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    # Same answer whether or not the email has an application.
    for token, application, organization in service.resend_links(db, str(body.email)):
        background_tasks.add_task(
            service.send_access_link_email, application.applicant_email, application.applicant_name,
            organization.legal_name, token)
    return ApplicationMessage(message=LINK_SENT)


@router.get("/current", response_model=ApplicationView, **CURRENT_DOCS)
def get_current(
    db: Session = Depends(get_db),
    current: tuple[OrganizationApplication, Organization] = Depends(get_application),
):
    return service.view(db, *current)


@router.patch("/current", response_model=ApplicationView, **UPDATE_DOCS)
def update_current(
    body: UpdateApplicationRequest,
    db: Session = Depends(get_db),
    current: tuple[OrganizationApplication, Organization] = Depends(get_application),
):
    application, organization = current
    service.update(db, application, organization, body)
    return service.view(db, application, organization)


@router.post("/current/submit", response_model=ApplicationView, **SUBMIT_DOCS)
def submit_current(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current: tuple[OrganizationApplication, Organization] = Depends(get_application),
):
    application, organization = current
    service.submit(db, application, organization)
    background_tasks.add_task(
        service.send_submitted_email, application.applicant_email, application.applicant_name,
        organization.legal_name)
    return service.view(db, application, organization)


def refuse_oversized_upload(content_length: int | None = Header(default=None)) -> None:
    """Refuse a huge body before reading it. The proxy in front must also cap the request size."""
    if content_length is not None and content_length > settings.document_max_bytes + 64 * 1024:
        raise ApiError("file_too_large", status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                       detail=f"El archivo supera el máximo de {settings.document_max_bytes // (1024 * 1024)} MB")


@router.get("/current/documents", response_model=list[DocumentSlot], **DOCUMENTS_DOCS)
def list_documents(
    db: Session = Depends(get_db),
    current: tuple[OrganizationApplication, Organization] = Depends(get_application),
):
    return documents.listing(db, current[1])


@router.put("/current/documents/{type_code}", response_model=UploadedDocument, **UPLOAD_DOCUMENT_DOCS)
@limiter.limit(application_uploads_limit)
def upload_document(
    request: Request,
    type_code: str,
    file: UploadFile = File(...),
    _: None = Depends(refuse_oversized_upload),
    db: Session = Depends(get_db),
    current: tuple[OrganizationApplication, Organization] = Depends(get_application),
):
    application, organization = current
    data = uploads.read_limited(file.file, settings.document_max_bytes)
    return documents.upload(db, application, organization, type_code, data, file.filename)


@router.delete("/current/documents/{type_code}", status_code=status.HTTP_204_NO_CONTENT, **DELETE_DOCUMENT_DOCS)
@limiter.limit(application_uploads_limit)
def delete_document(
    request: Request,
    type_code: str,
    db: Session = Depends(get_db),
    current: tuple[OrganizationApplication, Organization] = Depends(get_application),
):
    application, organization = current
    documents.delete(db, application, organization, type_code)
