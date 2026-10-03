from fastapi import APIRouter, BackgroundTasks, Depends, Header, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.rate_limit import applications_limit, limiter
from app.domains.applications import service
from app.domains.applications.docs import (
    ACCESS_LINK_DOCS,
    CURRENT_DOCS,
    START_DOCS,
    SUBMIT_DOCS,
    UPDATE_DOCS,
)
from app.domains.applications.models import OrganizationApplication
from app.domains.applications.schemas import (
    AccessLinkRequest,
    ApplicationMessage,
    ApplicationView,
    StartApplicationRequest,
    UpdateApplicationRequest,
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
def get_current(current: tuple[OrganizationApplication, Organization] = Depends(get_application)):
    return service.view(*current)


@router.patch("/current", response_model=ApplicationView, **UPDATE_DOCS)
def update_current(
    body: UpdateApplicationRequest,
    db: Session = Depends(get_db),
    current: tuple[OrganizationApplication, Organization] = Depends(get_application),
):
    application, organization = current
    service.update(db, application, organization, body)
    return service.view(application, organization)


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
    return service.view(application, organization)
