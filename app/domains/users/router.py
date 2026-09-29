import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.errors import ApiError
from app.core.permissions import (
    ORG_ADMINS,
    PRIVILEGED_USER_FIELDS,
    USERS_DIRECTORY,
    VERIFY_RECYCLERS,
    ensure_can_assign_role,
    forbidden,
    has_role,
    manageable_user_types,
    visible_user_types,
)
from app.core.security import get_current_user, require_roles
from app.domains.audit import service as audit
from app.domains.audit.actions import Action
from app.domains.auth import service as auth_service
from app.domains.users.docs import (
    GET_USER_DOCS,
    LIST_USERS_DOCS,
    UPDATE_RECYCLER_STATUS_DOCS,
    UPDATE_USER_DOCS,
)
from app.domains.users.enums import VerificationStatus
from app.domains.users.models import User
from app.domains.users.schemas import (
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
    limit: int = Query(default=20, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(*USERS_DIRECTORY)),
):
    visible = visible_user_types(actor)
    if user_type_code and user_type_code not in visible:
        raise forbidden("No puedes consultar usuarios de ese tipo", "user_type_not_visible")

    query = db.query(User).filter(User.user_type_code.in_(visible))

    if user_type_code:
        query = query.filter(User.user_type_code == user_type_code)
    if role_code:
        query = query.filter(User.role_code == role_code)
    if verification_status:
        query = query.filter(User.verification_status == verification_status)

    total = query.count()
    users = query.order_by(User.created_at.desc()).offset(offset).limit(limit).all()

    return UserListResponse(total=total, limit=limit, offset=offset, items=users)


@router.get("/{user_id}", response_model=UserDetailResponse, **GET_USER_DOCS)
def get_user(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    is_self = actor.id == user_id
    if not is_self and not has_role(actor, USERS_DIRECTORY):
        raise forbidden()

    user = db.get(User, user_id)
    if user is None:
        raise ApiError("user_not_found", status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado")
    if not is_self and user.user_type_code not in visible_user_types(actor):
        raise forbidden("No puedes consultar usuarios de ese tipo", "user_type_not_visible")
    return user


@router.patch("/{user_id}/verification-status", response_model=UserDetailResponse, **UPDATE_RECYCLER_STATUS_DOCS)
def update_recycler_status(
    user_id: uuid.UUID,
    request: UpdateRecyclerStatusRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*VERIFY_RECYCLERS)),
):
    user = db.get(User, user_id)
    if user is None:
        raise ApiError("user_not_found", status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado")
    if user.user_type_code != "recycler":
        raise ApiError("not_a_recycler", status_code=status.HTTP_400_BAD_REQUEST, detail="Este endpoint solo aplica para recicladores")

    user.verification_status = request.status

    activation_token = None
    if request.status == VerificationStatus.verified:
        # No password is ever derived from personal data: the recycler sets their own through
        # a one-time link emailed to them (POST /auth/activate).
        if not user.password_hash:
            activation_token = auth_service.issue_token(db, user, auth_service.ACTIVATE)
        user.verified_at = datetime.now(timezone.utc)
        user.verified_by = current_user.id
        user.rejection_reason = None
    elif request.status == VerificationStatus.rejected:
        user.rejection_reason = request.rejection_reason

    if request.status != VerificationStatus.pending:
        audit.record(
            db,
            Action.RECYCLER_VERIFIED if request.status == VerificationStatus.verified else Action.RECYCLER_REJECTED,
            actor=current_user, target_type="user", target_id=user.id,
            details={"activation_email_queued": activation_token is not None,
                     "has_reason": bool(request.rejection_reason)})
    db.commit()
    db.refresh(user)
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
    is_self = actor.id == user_id
    is_org_admin = has_role(actor, ORG_ADMINS)
    if not is_self and not is_org_admin:
        raise forbidden()

    user = db.get(User, user_id)
    if user is None:
        raise ApiError("user_not_found", status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado")

    if not is_self and user.user_type_code not in manageable_user_types(actor):
        raise forbidden("No puedes editar usuarios de ese tipo", "user_type_not_editable")

    privileged = request.model_fields_set & PRIVILEGED_USER_FIELDS
    if privileged:
        if not is_org_admin:
            raise forbidden("Solo un administrador puede modificar roles y permisos", "role_change_admin_only")
        if is_self:
            raise forbidden("No puedes modificar tus propios roles ni permisos", "cannot_change_own_role")
        if request.role_code is not None:
            ensure_can_assign_role(actor, request.role_code, user.user_type_code)

    previous_role = user.role_code
    for field in request.model_fields_set:
        setattr(user, field, getattr(request, field))

    if request.model_fields_set:
        # Field names only: the values are personal data and do not belong in the trail.
        audit.record(db, Action.USER_UPDATED, actor=actor, target_type="user", target_id=user.id,
                     details={"fields": sorted(request.model_fields_set), "self": is_self})
    if "role_code" in request.model_fields_set and user.role_code != previous_role:
        audit.record(db, Action.USER_ROLE_CHANGED, actor=actor, target_type="user", target_id=user.id,
                     details={"from": previous_role, "to": user.role_code})

    try:
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise ApiError("tax_id_already_registered", 
            status_code=status.HTTP_409_CONFLICT,
            detail="tax_id already registered",
        )

    return user
