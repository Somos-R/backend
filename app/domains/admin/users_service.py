"""User management from the backoffice: find people and act on their accounts.

Somos R sees every organization, so nothing here is scoped; instead every change is audited with who did
it and to whom, and a few rules keep it from being used to hurt oneself or to move people around. Each
function that changes data is a unit of work: it commits.
"""
import uuid
from datetime import datetime, timezone

from fastapi import status
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.core.pagination import paginate
from app.core.permissions import ROLE_USER_TYPE, STAFF_TYPES
from app.domains.audit import service as audit
from app.domains.audit.actions import Action
from app.domains.auth import service as auth_service
from app.domains.auth.models import MfaCredential
from app.domains.catalogs.models import Role
from app.domains.organizations.enums import OrganizationStatus
from app.domains.organizations.models import Organization
from app.domains.users import service as users_service
from app.domains.users.enums import VerificationStatus
from app.domains.users.models import User
from app.domains.users.schemas import UserDetailResponse


def _get(db: Session, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise ApiError("user_not_found", status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado")
    return user


def is_locked(user: User) -> bool:
    return auth_service.is_locked(user)


def is_pending_activation(user: User) -> bool:
    return user.password_hash == "" and user.user_type_code in auth_service.ACTIVATABLE_TYPES


def summary(user: User) -> dict:
    return {
        "id": user.id, "email": user.email, "full_name": user.full_name, "user_type_code": user.user_type_code,
        "role_code": user.role_code, "organization_id": user.organization_id, "is_active": user.is_active,
        "verification_status": user.verification_status, "email_verified": user.email_verified_at is not None,
        "locked": is_locked(user), "pending_activation": is_pending_activation(user), "created_at": user.created_at,
    }


# --- Finding people ---------------------------------------------------------------------

def list_users(
    db: Session, *, q: str | None, user_type_code: str | None, role_code: str | None,
    organization_id: uuid.UUID | None, is_active: bool | None, locked: bool | None,
    pending_activation: bool | None, verification_status: VerificationStatus | None, limit: int, offset: int,
) -> tuple[int, list[dict]]:
    query = select(User)
    text_match = users_service.search_clause(q)
    if text_match is not None:
        query = query.where(text_match)
    if user_type_code:
        query = query.where(User.user_type_code == user_type_code)
    if role_code:
        query = query.where(User.role_code == role_code)
    if organization_id:
        query = query.where(User.organization_id == organization_id)
    if is_active is not None:
        query = query.where(User.is_active.is_(is_active))
    if verification_status:
        query = query.where(User.verification_status == verification_status)
    if locked is not None:
        now = datetime.now(timezone.utc)
        is_locked_now = and_(User.locked_until.is_not(None), User.locked_until > now)
        query = query.where(is_locked_now if locked else ~is_locked_now)
    if pending_activation is not None:
        waiting = and_(User.password_hash == "", User.user_type_code.in_(auth_service.ACTIVATABLE_TYPES))
        query = query.where(waiting if pending_activation else ~waiting)
    total, users = paginate(db, query, User.created_at.desc(), User.id, limit=limit, offset=offset)
    return total, [summary(u) for u in users]


def get_detail(db: Session, actor: User, user_id: uuid.UUID) -> dict:
    """The whole profile. Opening it is audited: it is the place where personal data is shown in full."""
    user = _get(db, user_id)
    credential = db.get(MfaCredential, user.id)
    organization = db.get(Organization, user.organization_id) if user.organization_id else None
    audit.record(db, Action.ADMIN_USER_VIEWED, actor=actor, target_type="user", target_id=user.id)
    db.commit()
    data = UserDetailResponse.model_validate(user).model_dump()
    return {
        **data, "locked": is_locked(user), "locked_until": user.locked_until,
        "failed_login_attempts": user.failed_login_attempts, "pending_activation": is_pending_activation(user),
        "mfa_enabled": bool(credential and credential.enabled_at),
        "organization_name": organization.legal_name if organization else None,
    }


# --- Acting on an account ---------------------------------------------------------------

def set_active(db: Session, actor: User, user_id: uuid.UUID, active: bool, reason: str | None) -> User:
    user = _get(db, user_id)
    if user.id == actor.id:
        raise ApiError("cannot_change_own_status", status_code=status.HTTP_403_FORBIDDEN,
                       detail="No puedes desactivar tu propia cuenta")
    if user.is_active == active:
        return user  # already so: nothing to do, nothing to record
    user.is_active = active
    if not active:
        auth_service.revoke_all_sessions(db, user)  # signed out everywhere, now
    audit.record(
        db, Action.USER_ACTIVATED if active else Action.USER_DEACTIVATED, actor=actor,
        target_type="user", target_id=user.id, details={"reason": reason} if reason else None)
    db.commit()
    db.refresh(user)
    return user


def unlock(db: Session, actor: User, user_id: uuid.UUID) -> User:
    user = _get(db, user_id)
    auth_service.clear_login_failures(user)
    audit.record(db, Action.USER_UNLOCKED, actor=actor, target_type="user", target_id=user.id)
    db.commit()
    db.refresh(user)
    return user


def revoke_sessions(db: Session, actor: User, user_id: uuid.UUID) -> User:
    user = _get(db, user_id)
    auth_service.revoke_all_sessions(db, user)
    audit.record(db, Action.USER_SESSIONS_REVOKED, actor=actor, target_type="user", target_id=user.id)
    db.commit()
    db.refresh(user)
    return user


def change_role(db: Session, actor: User, user_id: uuid.UUID, role_code: str) -> User:
    """Give an ECA or Association staff member another existing role of their own organization type."""
    user = _get(db, user_id)
    if user.user_type_code not in STAFF_TYPES:
        raise ApiError("role_not_editable", status_code=status.HTTP_409_CONFLICT,
                       detail="Solo el personal de ECA y Asociación tiene roles que se puedan cambiar")
    role = db.scalars(select(Role).where(Role.code == role_code, Role.is_active.is_(True))).first()
    if role is None or ROLE_USER_TYPE.get(role_code) != user.user_type_code:
        raise ApiError("invalid_role", status_code=422,
                       detail=f"role_code '{role_code}' no es válido para un usuario de tipo '{user.user_type_code}'")
    previous = user.role_code
    if previous != role_code:
        user.role_code = role_code
        audit.record(db, Action.USER_ROLE_CHANGED, actor=actor, target_type="user", target_id=user.id,
                     details={"from": previous, "to": role_code})
        db.commit()
        db.refresh(user)
    return user


def assign_organization(db: Session, actor: User, user_id: uuid.UUID, organization_id: uuid.UUID) -> User:
    """Give an organization to staff who have none (accounts that predate organizations).

    Moving someone from one organization to another is not supported: it would hand them another
    organization's data. Deactivate and invite them again instead.
    """
    user = _get(db, user_id)
    if user.user_type_code not in STAFF_TYPES:
        raise ApiError("organization_not_applicable", status_code=status.HTTP_409_CONFLICT,
                       detail="Solo el personal de ECA y Asociación pertenece a una organización")
    if user.organization_id is not None:
        raise ApiError("already_in_organization", status_code=status.HTTP_409_CONFLICT,
                       detail="Esta persona ya pertenece a una organización")
    organization = db.get(Organization, organization_id)
    if organization is None:
        raise ApiError("organization_not_found", status_code=status.HTTP_404_NOT_FOUND,
                       detail="Organización no encontrada")
    if organization.type.value != user.user_type_code:
        raise ApiError("organization_type_mismatch", status_code=422,
                       detail="La organización no es del mismo tipo que la persona")
    if organization.status != OrganizationStatus.approved:
        raise ApiError("organization_not_active", status_code=status.HTTP_409_CONFLICT,
                       detail="La organización no está aprobada")
    user.organization_id = organization.id
    audit.record(db, Action.USER_ORGANIZATION_ASSIGNED, actor=actor, target_type="user", target_id=user.id,
                 details={"organization_id": str(organization.id)})
    db.commit()
    db.refresh(user)
    return user


def resend_activation(db: Session, actor: User, user_id: uuid.UUID) -> tuple[User, str, str | None]:
    """A new activation link for someone who has not chosen a password yet.

    Returns the user, the token to email and the organization name (None for recyclers).
    """
    user = _get(db, user_id)
    if not is_pending_activation(user) or not user.is_active:
        raise ApiError("invitation_not_pending", status_code=status.HTTP_409_CONFLICT,
                       detail="Esta persona no tiene una activación pendiente")
    organization_name = None
    if user.user_type_code == "recycler":
        if user.verification_status != VerificationStatus.verified:
            raise ApiError("not_verified", status_code=status.HTTP_409_CONFLICT,
                           detail="El reciclador aún no está verificado")
    else:
        organization = db.get(Organization, user.organization_id) if user.organization_id else None
        if organization is None:
            raise ApiError("no_organization", status_code=status.HTTP_409_CONFLICT,
                           detail="Asigna primero una organización a esta persona")
        organization_name = organization.legal_name
    token = auth_service.issue_token(db, user, auth_service.ACTIVATE)
    audit.record(db, Action.USER_INVITATION_RESENT, actor=actor, target_type="user", target_id=user.id,
                 details={"via": "backoffice"})
    db.commit()
    return user, token, organization_name

