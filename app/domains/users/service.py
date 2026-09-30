"""User directory, recycler verification and profile updates.

Each function that changes data commits: it is a unit of work the router only translates to HTTP.
"""
import uuid
from datetime import datetime, timezone

from fastapi import status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.core.errors import ApiError
from app.core.pagination import paginate
from app.core.permissions import (
    ORG_ADMINS,
    PRIVILEGED_USER_FIELDS,
    STAFF_TYPES,
    USERS_DIRECTORY,
    ensure_can_assign_role,
    forbidden,
    has_role,
    in_scope,
    manageable_user_types,
    visible_user_types,
)
from app.domains.audit import service as audit
from app.domains.audit.actions import Action
from app.domains.auth import service as auth_service
from app.domains.catalogs.models import DocumentType, Role
from app.domains.organizations.enums import OrganizationStatus
from app.domains.organizations.models import Organization
from app.domains.users.enums import VerificationStatus
from app.domains.users.models import User
from app.domains.users.schemas import (
    InviteStaffRequest,
    UpdateRecyclerStatusRequest,
    UpdateUserRequest,
)

MIN_SEARCH_LENGTH = 2
_SEARCH_COLUMNS = (User.full_name, User.id_number, User.email)


def _search_term(q: str | None) -> str | None:
    """`%text%` for a case- and accent-insensitive "contains", or None when q is too short to use.

    `%`, `_` and backslash in the input are escaped: they are characters to find, never wildcards.
    """
    text = (q or "").strip()
    if len(text) < MIN_SEARCH_LENGTH:
        return None
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def search_clause(q: str | None) -> ColumnElement[bool] | None:
    """SQL for the text search over name, document and email; None when `q` is empty or too short."""
    term = _search_term(q)
    if term is None:
        return None
    pattern = func.unaccent(term)
    return or_(*(func.unaccent(column).ilike(pattern, escape="\\") for column in _SEARCH_COLUMNS))


def _in_scope_clause(actor: User) -> ColumnElement[bool]:
    """SQL twin of `in_scope`: whoever is not tied to an organization, plus those of the actor's own."""
    scoped = set(STAFF_TYPES) | ({"recycler"} if actor.user_type_code == "association" else set())
    if actor.organization_id is None:
        return User.user_type_code.not_in(scoped)
    return or_(
        User.user_type_code.not_in(scoped),
        and_(User.user_type_code.in_(scoped), User.organization_id == actor.organization_id),
    )


def _user_not_found() -> ApiError:
    return ApiError("user_not_found", status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado")


def list_users(
    db: Session,
    actor: User,
    user_type_code: str | None,
    role_code: str | None,
    verification_status: str | None,
    q: str | None,
    limit: int,
    offset: int,
) -> tuple[int, list[User]]:
    """The page of users the actor may see, and the total that match."""
    visible = visible_user_types(actor)
    if user_type_code and user_type_code not in visible:
        raise forbidden("No puedes consultar usuarios de ese tipo", "user_type_not_visible")

    query = select(User).where(User.user_type_code.in_(visible), _in_scope_clause(actor))
    if user_type_code:
        query = query.where(User.user_type_code == user_type_code)
    if role_code:
        query = query.where(User.role_code == role_code)
    if verification_status:
        query = query.where(User.verification_status == verification_status)
    text_match = search_clause(q)
    if text_match is not None:
        query = query.where(text_match)

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
    # Someone else's staff answers like a missing user: it does not confirm they exist.
    if not is_self and not in_scope(actor, user):
        raise _user_not_found()
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
    if not in_scope(actor, user):  # only the association the recycler belongs to verifies them
        raise _user_not_found()

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
    if not is_self and not in_scope(actor, user):
        raise _user_not_found()

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


# --- Staff invitations ------------------------------------------------------------------
# The organization's admin names the person and their role; the person chooses their own password
# through an emailed one-time link (the same activation used for recyclers). Nobody ever types
# someone else's password.

def _operating_organization(db: Session, actor: User) -> Organization:
    """The actor's organization, if it may operate. Fails closed for accounts with none."""
    organization = db.get(Organization, actor.organization_id) if actor.organization_id else None
    if organization is None:
        raise ApiError("no_organization", status_code=status.HTTP_403_FORBIDDEN,
                       detail="Tu cuenta no está asociada a una organización, así que no puede invitar personal")
    if organization.status != OrganizationStatus.approved:
        raise ApiError("organization_not_active", status_code=status.HTTP_403_FORBIDDEN,
                       detail="Tu organización no está activa, así que no puede invitar personal")
    return organization


def invite_staff(db: Session, actor: User, request: InviteStaffRequest) -> tuple[User, str, str]:
    """Create the account (no password) inside the actor's organization.

    Returns the user, the activation token to email and the organization's name.
    """
    ensure_can_assign_role(actor, request.role_code, actor.user_type_code)
    organization = _operating_organization(db, actor)

    role = db.scalars(select(Role).where(Role.code == request.role_code, Role.is_active.is_(True))).first()
    if role is None:
        raise ApiError("invalid_role", status_code=422,
                       detail=f"role_code '{request.role_code}' no es válido o está inactivo")
    if db.get(DocumentType, request.id_type) is None:
        raise ApiError("invalid_id_type", status_code=422, detail=f"id_type '{request.id_type}' no es válido")

    user = User(
        email=request.email, full_name=request.full_name.strip(), phone=request.phone,
        id_type=request.id_type, id_number=request.id_number,
        user_type_code=actor.user_type_code, role_code=request.role_code,
        organization_id=organization.id, password_hash="",
        # Legacy per-person copies of the organization's data (the organization is the source of truth).
        association_nit=organization.tax_id if actor.user_type_code == "association" else None,
        legal_representative=organization.legal_representative if actor.user_type_code == "association" else None,
    )
    try:
        db.add(user)
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ApiError("account_already_exists", status_code=status.HTTP_409_CONFLICT,
                       detail="Email or ID number already registered")
    token = auth_service.issue_token(db, user, auth_service.ACTIVATE)
    # The role, never the address or the document: those are personal data.
    audit.record(db, Action.USER_INVITED, actor=actor, target_type="user", target_id=user.id,
                 details={"role_code": request.role_code})
    db.commit()
    db.refresh(user)
    return user, token, organization.legal_name


def resend_invitation(db: Session, actor: User, user_id: uuid.UUID) -> tuple[User, str, str]:
    """A fresh activation link for someone invited who has not accepted yet (the old link stops working)."""
    organization = _operating_organization(db, actor)
    user = db.get(User, user_id)
    if (
        user is None or user.user_type_code not in STAFF_TYPES
        or user.user_type_code not in manageable_user_types(actor) or not in_scope(actor, user)
    ):
        raise _user_not_found()
    if user.password_hash:
        raise ApiError("invitation_not_pending", status_code=status.HTTP_409_CONFLICT,
                       detail="Esta persona no tiene una invitación pendiente")
    token = auth_service.issue_token(db, user, auth_service.ACTIVATE)
    audit.record(db, Action.USER_INVITATION_RESENT, actor=actor, target_type="user", target_id=user.id)
    db.commit()
    return user, token, organization.legal_name
