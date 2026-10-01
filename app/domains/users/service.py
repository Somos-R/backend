"""User directory, recycler verification and profile updates.

Each function that changes data commits: it is a unit of work the router only translates to HTTP.
"""
import uuid
from datetime import datetime, timezone

from fastapi import status
from sqlalchemy import and_, false, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.core import search
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
from app.domains.organizations import scope
from app.domains.organizations.enums import OrganizationStatus
from app.domains.organizations.models import Organization
from app.domains.users.enums import VerificationStatus
from app.domains.users.models import User
from app.domains.users.schemas import (
    InviteStaffRequest,
    UpdateRecyclerStatusRequest,
    UpdateUserRequest,
)

_SEARCH_COLUMNS = (User.full_name, User.id_number, User.email)


def search_clause(q: str | None) -> ColumnElement[bool] | None:
    """SQL for the text search over name, document and email; None when `q` is empty or too short."""
    return search.contains(q, _SEARCH_COLUMNS)


def _in_scope_clause(actor: User) -> ColumnElement[bool]:
    """SQL twin of `_reachable`: whoever is not tied to an organization, plus what the actor's own reaches.

    Staff are reached from their own organization only; a recycler, from the association they belong to and
    from the ECAs linked to it.
    """
    others = User.user_type_code.not_in(set(STAFF_TYPES) | {"recycler"})
    org = actor.organization_id
    staff = and_(User.user_type_code.in_(STAFF_TYPES), User.organization_id == org) if org is not None else false()
    if org is None and actor.user_type_code in ("eca", "association"):
        recyclers: ColumnElement[bool] = false()
    elif actor.user_type_code == "association":
        recyclers = and_(User.user_type_code == "recycler", User.organization_id == org)
    elif actor.user_type_code == "eca":
        recyclers = scope.recycler_linked_to(org)  # type: ignore[arg-type]
    else:
        recyclers = User.user_type_code == "recycler"
    return or_(others, staff, recyclers)


def _reachable(db: Session, actor: User, target: User) -> bool:
    """`in_scope`, plus the link rule that needs the database: an ECA reaches the recyclers of the
    associations it is actively linked to, and only those."""
    if not in_scope(actor, target):
        return False
    if actor.user_type_code == "eca" and target.user_type_code == "recycler":
        if actor.organization_id is None:
            return False
        return db.scalar(select(User.id).where(
            User.id == target.id, scope.recycler_linked_to(actor.organization_id))) is not None
    return True


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
    if not is_self and not _reachable(db, actor, user):
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
    if not _reachable(db, actor, user):  # only the association the recycler belongs to verifies them
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
    if not is_self and not _reachable(db, actor, user):
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


def lookup_recycler(db: Session, actor: User, document: str, id_type: str | None) -> dict:
    """Find a registered recycler by document, whatever their association: an ECA receives material from
    anyone, so it must be able to identify them. Returns only what is needed to weigh."""
    if actor.organization_id is None:
        raise ApiError("no_organization", status_code=status.HTTP_403_FORBIDDEN,
                       detail="Tu cuenta no está asociada a una organización")
    query = select(User).where(User.user_type_code == "recycler", User.id_number == document.strip())
    if id_type:
        query = query.where(User.id_type == id_type)
    recycler = db.scalars(query.order_by(User.created_at, User.id)).first()
    if recycler is None:
        raise ApiError("recycler_not_found", status_code=status.HTTP_404_NOT_FOUND, detail="Reciclador no encontrado")
    association = db.get(Organization, recycler.organization_id) if recycler.organization_id else None
    return {
        "id": recycler.id, "full_name": recycler.full_name, "id_type": recycler.id_type,
        "id_number": recycler.id_number, "is_active": recycler.is_active,
        "verification_status": recycler.verification_status, "association": association,
        "affiliation": scope.affiliation_of(db, actor.organization_id, recycler),
    }


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


def set_staff_active(db: Session, actor: User, user_id: uuid.UUID, active: bool, reason: str | None) -> User:
    """Deactivate or reactivate someone of the actor's own organization. Deactivating signs them out everywhere.

    Only the organization's staff: a recycler is handled by their association (verification) or by Somos R.
    Someone else's staff answers like a missing user; nobody changes their own status.
    """
    user = db.get(User, user_id)
    if user is None or user.user_type_code not in ("eca", "association") or not in_scope(actor, user):
        raise _user_not_found()
    if user.id == actor.id:
        raise ApiError("cannot_change_own_status", status_code=status.HTTP_403_FORBIDDEN,
                       detail="No puedes desactivar tu propia cuenta")
    if user.is_active == active:
        return user  # already so: nothing to do, nothing to record
    user.is_active = active
    if not active:
        auth_service.revoke_all_sessions(db, user)
    audit.record(
        db, Action.USER_ACTIVATED if active else Action.USER_DEACTIVATED, actor=actor,
        target_type="user", target_id=user.id, details={"reason": reason} if reason else None)
    db.commit()
    db.refresh(user)
    return user


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
        or user.user_type_code not in manageable_user_types(actor) or not _reachable(db, actor, user)
    ):
        raise _user_not_found()
    if user.password_hash:
        raise ApiError("invitation_not_pending", status_code=status.HTTP_409_CONFLICT,
                       detail="Esta persona no tiene una invitación pendiente")
    token = auth_service.issue_token(db, user, auth_service.ACTIVATE)
    audit.record(db, Action.USER_INVITATION_RESENT, actor=actor, target_type="user", target_id=user.id)
    db.commit()
    return user, token, organization.legal_name
