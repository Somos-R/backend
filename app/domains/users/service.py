"""User directory, recycler verification and profile updates.

Each function that changes data commits: it is a unit of work the router only translates to HTTP.
"""
import uuid
from datetime import datetime, timezone

from fastapi import status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.core.pagination import paginate
from app.core.permissions import (
    ORG_ADMINS,
    PRIVILEGED_USER_FIELDS,
    USERS_DIRECTORY,
    ensure_can_assign_role,
    forbidden,
    has_role,
    manageable_user_types,
    visible_user_types,
)
from app.domains.audit import service as audit
from app.domains.audit.actions import Action
from app.domains.auth import service as auth_service
from app.domains.users.enums import VerificationStatus
from app.domains.users.models import User
from app.domains.users.schemas import UpdateRecyclerStatusRequest, UpdateUserRequest


def _user_not_found() -> ApiError:
    return ApiError("user_not_found", status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado")


def list_users(
    db: Session,
    actor: User,
    user_type_code: str | None,
    role_code: str | None,
    verification_status: str | None,
    limit: int,
    offset: int,
) -> tuple[int, list[User]]:
    """The page of users the actor may see, and the total that match."""
    visible = visible_user_types(actor)
    if user_type_code and user_type_code not in visible:
        raise forbidden("No puedes consultar usuarios de ese tipo", "user_type_not_visible")

    query = select(User).where(User.user_type_code.in_(visible))
    if user_type_code:
        query = query.where(User.user_type_code == user_type_code)
    if role_code:
        query = query.where(User.role_code == role_code)
    if verification_status:
        query = query.where(User.verification_status == verification_status)

    return paginate(db, query, User.created_at.desc(), User.id, limit=limit, offset=offset)


def get_user(db: Session, actor: User, user_id: uuid.UUID) -> User:
    is_self = actor.id == user_id
    if not is_self and not has_role(actor, USERS_DIRECTORY):
        raise forbidden()

    user = db.get(User, user_id)
    if user is None:
        raise _user_not_found()
    if not is_self and user.user_type_code not in visible_user_types(actor):
        raise forbidden("No puedes consultar usuarios de ese tipo", "user_type_not_visible")
    return user


def set_verification_status(
    db: Session, actor: User, user_id: uuid.UUID, request: UpdateRecyclerStatusRequest
) -> tuple[User, str | None]:
    """Verify, reject or reset a recycler. Returns the user and the activation token to email (if any)."""
    user = db.get(User, user_id)
    if user is None:
        raise _user_not_found()
    if user.user_type_code != "recycler":
        raise ApiError("not_a_recycler", status_code=status.HTTP_400_BAD_REQUEST,
                       detail="Este endpoint solo aplica para recicladores")

    user.verification_status = request.status

    activation_token = None
    if request.status == VerificationStatus.verified:
        # No password is ever derived from personal data: the recycler sets their own through
        # a one-time link emailed to them (POST /auth/activate).
        if not user.password_hash:
            activation_token = auth_service.issue_token(db, user, auth_service.ACTIVATE)
        user.verified_at = datetime.now(timezone.utc)
        user.verified_by = actor.id
        user.rejection_reason = None
    elif request.status == VerificationStatus.rejected:
        user.rejection_reason = request.rejection_reason

    if request.status != VerificationStatus.pending:
        audit.record(
            db,
            Action.RECYCLER_VERIFIED if request.status == VerificationStatus.verified else Action.RECYCLER_REJECTED,
            actor=actor, target_type="user", target_id=user.id,
            details={"activation_email_queued": activation_token is not None,
                     "has_reason": bool(request.rejection_reason)})
    db.commit()
    db.refresh(user)
    return user, activation_token


def update_user(db: Session, actor: User, user_id: uuid.UUID, request: UpdateUserRequest) -> User:
    is_self = actor.id == user_id
    is_org_admin = has_role(actor, ORG_ADMINS)
    if not is_self and not is_org_admin:
        raise forbidden()

    user = db.get(User, user_id)
    if user is None:
        raise _user_not_found()

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
        raise ApiError("tax_id_already_registered", status_code=status.HTTP_409_CONFLICT,
                       detail="tax_id already registered")
    return user
