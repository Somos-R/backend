import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.permissions import ORG_ADMINS, USERS_DIRECTORY, VERIFY_RECYCLERS
from app.core.rate_limit import limiter, register_limit
from app.core.security import get_current_user, require_roles
from app.domains.auth import service as auth_service
from app.domains.users import service as users_service
from app.domains.users.docs import (
    GET_USER_DOCS,
    INVITE_STAFF_DOCS,
    LIST_USERS_DOCS,
    RESEND_INVITATION_DOCS,
    UPDATE_RECYCLER_STATUS_DOCS,
    UPDATE_USER_DOCS,
)
from app.domains.users.models import User
from app.domains.users.schemas import (
    InviteStaffRequest,
    UpdateRecyclerStatusRequest,
    UpdateUserRequest,
    UserDetailResponse,
    UserListResponse,
)

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=UserListResponse, **LIST_USERS_DOCS)
def list_users(
    user_type_code: str | None = Query(default=None),
    role_code: str | None = Query(default=None),
    verification_status: str | None = Query(default=None),
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=20, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*USERS_DIRECTORY)),
):
    total, users = users_service.list_users(
        db, actor, user_type_code, role_code, verification_status, q, limit, offset)
    return UserListResponse(total=total, limit=limit, offset=offset, items=users)


@router.post("/invitations", response_model=UserDetailResponse, status_code=status.HTTP_201_CREATED, **INVITE_STAFF_DOCS)
@limiter.limit(register_limit)
def invite_staff(
    request: Request,
    body: InviteStaffRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*ORG_ADMINS)),
):
    user, token, organization_name = users_service.invite_staff(db, actor, body)
    background_tasks.add_task(
        auth_service.send_staff_invitation_email, user.email, user.full_name, organization_name, token)
    return user


@router.post("/{user_id}/invitation/resend", response_model=UserDetailResponse, **RESEND_INVITATION_DOCS)
@limiter.limit(register_limit)
def resend_invitation(
    request: Request,
    user_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*ORG_ADMINS)),
):
    user, token, organization_name = users_service.resend_invitation(db, actor, user_id)
    background_tasks.add_task(
        auth_service.send_staff_invitation_email, user.email, user.full_name, organization_name, token)
    return user


@router.get("/{user_id}", response_model=UserDetailResponse, **GET_USER_DOCS)
def get_user(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    return users_service.get_user(db, actor, user_id)


@router.patch("/{user_id}/verification-status", response_model=UserDetailResponse, **UPDATE_RECYCLER_STATUS_DOCS)
def update_recycler_status(
    user_id: uuid.UUID,
    request: UpdateRecyclerStatusRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*VERIFY_RECYCLERS)),
):
    user, activation_token = users_service.set_verification_status(db, current_user, user_id, request)
    if activation_token:
        background_tasks.add_task(
            auth_service.send_activation_email, user.email, user.full_name, activation_token)
    return user


@router.patch("/{user_id}", response_model=UserDetailResponse, **UPDATE_USER_DOCS)
def update_user(
    user_id: uuid.UUID,
    request: UpdateUserRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    return users_service.update_user(db, actor, user_id, request)
