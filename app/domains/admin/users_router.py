import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.network import enforce_admin_network
from app.core.rate_limit import admin_limit, limiter
from app.core.security import require_capability
from app.domains.admin import users_service as service
from app.domains.admin.users_schemas import (
    AdminUserDetail,
    AdminUserListResponse,
    AdminUserSummary,
    AssignOrganizationRequest,
    ChangeRoleRequest,
    SetActiveRequest,
)
from app.domains.auth import service as auth_service
from app.domains.users.enums import VerificationStatus
from app.domains.users.models import User

# The management of accounts: needs the `users.manage` capability, a backoffice token and an allowed network.
router = APIRouter(prefix="/admin/users", tags=["admin"], dependencies=[Depends(enforce_admin_network)])
Manager = Depends(require_capability("users.manage"))


@router.get("", response_model=AdminUserListResponse)
@limiter.limit(admin_limit)
def list_users(
    request: Request,
    q: str | None = Query(default=None, max_length=100),
    user_type_code: str | None = Query(default=None, max_length=20),
    role_code: str | None = Query(default=None, max_length=20),
    organization_id: uuid.UUID | None = Query(default=None),
    is_active: bool | None = Query(default=None),
    locked: bool | None = Query(default=None),
    pending_activation: bool | None = Query(default=None),
    verification_status: VerificationStatus | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _: User = Manager,
):
    """Everyone, across organizations. `q` searches name, document and email (no case, no accents)."""
    total, items = service.list_users(
        db, q=q, user_type_code=user_type_code, role_code=role_code, organization_id=organization_id,
        is_active=is_active, locked=locked, pending_activation=pending_activation,
        verification_status=verification_status, limit=limit, offset=offset)
    return AdminUserListResponse(total=total, limit=limit, offset=offset, items=items)


@router.get("/{user_id}", response_model=AdminUserDetail)
@limiter.limit(admin_limit)
def get_user(request: Request, user_id: uuid.UUID, db: Session = Depends(get_db), actor: User = Manager):
    """The full profile and the security state of the account. Opening it is audited."""
    return service.get_detail(db, actor, user_id)


@router.patch("/{user_id}/status", response_model=AdminUserSummary)
@limiter.limit(admin_limit)
def set_status(
    request: Request, user_id: uuid.UUID, body: SetActiveRequest,
    db: Session = Depends(get_db), actor: User = Manager,
):
    """Deactivate or reactivate an account. Deactivating also ends every session it has. Not your own."""
    return service.summary(service.set_active(db, actor, user_id, body.is_active, body.reason))


@router.post("/{user_id}/unlock", response_model=AdminUserSummary)
@limiter.limit(admin_limit)
def unlock(request: Request, user_id: uuid.UUID, db: Session = Depends(get_db), actor: User = Manager):
    """Lift the temporary block after failed sign-ins."""
    return service.summary(service.unlock(db, actor, user_id))


@router.post("/{user_id}/sessions/revoke", response_model=AdminUserSummary)
@limiter.limit(admin_limit)
def revoke_sessions(request: Request, user_id: uuid.UUID, db: Session = Depends(get_db), actor: User = Manager):
    """Sign the person out everywhere: every refresh token and every access token issued so far."""
    return service.summary(service.revoke_sessions(db, actor, user_id))


@router.post("/{user_id}/invitation/resend", response_model=AdminUserSummary)
@limiter.limit(admin_limit)
def resend_activation(
    request: Request, user_id: uuid.UUID, background_tasks: BackgroundTasks,
    db: Session = Depends(get_db), actor: User = Manager,
):
    """A new activation link for an invited person, or a verified recycler, who has not set a password."""
    user, token, organization_name = service.resend_activation(db, actor, user_id)
    if organization_name is None:
        background_tasks.add_task(auth_service.send_activation_email, user.email, user.full_name, token)
    else:
        background_tasks.add_task(
            auth_service.send_staff_invitation_email, user.email, user.full_name, organization_name, token)
    return service.summary(user)


@router.patch("/{user_id}/role", response_model=AdminUserSummary)
@limiter.limit(admin_limit)
def change_role(
    request: Request, user_id: uuid.UUID, body: ChangeRoleRequest,
    db: Session = Depends(get_db), actor: User = Manager,
):
    """Another existing role for an ECA or Association staff member (it must fit their type)."""
    return service.summary(service.change_role(db, actor, user_id, body.role_code))


@router.put("/{user_id}/organization", response_model=AdminUserSummary)
@limiter.limit(admin_limit)
def assign_organization(
    request: Request, user_id: uuid.UUID, body: AssignOrganizationRequest,
    db: Session = Depends(get_db), actor: User = Manager,
):
    """Give an organization to staff who have none. It cannot move someone between organizations."""
    return service.summary(service.assign_organization(db, actor, user_id, body.organization_id))
