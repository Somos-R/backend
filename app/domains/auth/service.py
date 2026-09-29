"""One-time tokens (activation, email verification, password reset) and their emails."""
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import metrics
from app.core.config import settings
from app.core.email import send_email
from app.core.errors import ApiError
from app.core.permissions import ORG_ADMINS, ensure_can_assign_role, has_role
from app.core.security import create_access_token, hash_password, verify_password
from app.domains.audit import service as audit
from app.domains.audit.actions import FAILURE, Action
from app.domains.auth.models import OneTimeToken, RefreshToken, RevokedToken
from app.domains.catalogs.models import Role
from app.domains.users.enums import VerificationStatus
from app.domains.users.models import User

ACTIVATE = "activate"
VERIFY_EMAIL = "verify_email"
RESET_PASSWORD = "reset_password"

_TTL_MINUTES = {
    ACTIVATE: lambda: settings.activation_token_minutes,
    VERIFY_EMAIL: lambda: settings.email_verification_token_minutes,
    RESET_PASSWORD: lambda: settings.password_reset_token_minutes,
}

def is_locked(user: User) -> bool:
    return user.locked_until is not None and user.locked_until > datetime.now(timezone.utc)


def register_failed_login(user: User) -> None:
    """Count a failure; from the Nth consecutive one on, lock for 1, 2, 4, ... minutes (capped)."""
    user.failed_login_attempts += 1
    over = user.failed_login_attempts - settings.login_max_attempts
    if over >= 0:
        minutes = min(2 ** over, settings.login_lock_max_minutes)
        user.locked_until = datetime.now(timezone.utc) + timedelta(minutes=minutes)


def clear_login_failures(user: User) -> None:
    user.failed_login_attempts = 0
    user.locked_until = None


INVALID_LINK = "El enlace es inválido, ya fue usado o expiró"


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def issue_token(db: Session, user: User, purpose: str) -> str:
    """Create a token for `purpose` and return the raw value (shown once, never stored).

    Any earlier unused token of the same purpose is invalidated, so only the newest link works.
    Caller commits.
    """
    now = datetime.now(timezone.utc)
    db.execute(
        update(OneTimeToken)
        .where(
            OneTimeToken.user_id == user.id,
            OneTimeToken.purpose == purpose,
            OneTimeToken.used_at.is_(None),
        )
        .values(used_at=now)
        .execution_options(synchronize_session=False)
    )

    raw = secrets.token_urlsafe(32)
    db.add(OneTimeToken(
        user_id=user.id,
        purpose=purpose,
        token_hash=_hash(raw),
        expires_at=now + timedelta(minutes=_TTL_MINUTES[purpose]()),
    ))
    db.flush()
    return raw


def consume_token(db: Session, raw: str, purpose: str) -> User:
    """Validate and burn a token, returning its user. Raises 400 for any invalid case."""
    invalid = ApiError("invalid_link", status_code=status.HTTP_400_BAD_REQUEST, detail=INVALID_LINK)

    row = db.scalars(
        select(OneTimeToken)
        .where(OneTimeToken.token_hash == _hash(raw), OneTimeToken.purpose == purpose)
        .with_for_update()
    ).first()
    now = datetime.now(timezone.utc)
    if row is None or row.used_at is not None or row.expires_at <= now:
        raise invalid

    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise invalid

    row.used_at = now
    return user


# --- Emails (called from background tasks with plain values, after the commit) ---

def _link(path: str, token: str) -> str:
    return f"{settings.frontend_url.rstrip('/')}/{path}?token={token}"


def send_activation_email(to: str, full_name: str, token: str) -> None:
    send_email(
        to,
        "Activa tu cuenta en Somos R",
        f"Hola {full_name},\n\n"
        "Tu perfil fue verificado. Crea tu contraseña para empezar a usar Somos R:\n"
        f"{_link('activate', token)}\n\n"
        f"El enlace es de un solo uso y vence en {settings.activation_token_minutes // 60} horas.",
    )


def send_verification_email(to: str, full_name: str, token: str) -> None:
    send_email(
        to,
        "Confirma tu correo en Somos R",
        f"Hola {full_name},\n\n"
        "Confirma tu correo electrónico con este enlace:\n"
        f"{_link('verify-email', token)}\n\n"
        "Si no creaste una cuenta en Somos R, ignora este mensaje.",
    )


def send_password_reset_email(to: str, full_name: str, token: str) -> None:
    send_email(
        to,
        "Restablece tu contraseña de Somos R",
        f"Hola {full_name},\n\n"
        "Recibimos una solicitud para restablecer tu contraseña:\n"
        f"{_link('reset-password', token)}\n\n"
        f"El enlace es de un solo uso y vence en {settings.password_reset_token_minutes} minutos. "
        "Si no fuiste tú, ignora este mensaje; tu contraseña no cambiará.",
    )


# --- Refresh tokens (rotating, reuse-detecting) ---------------------------------------

INVALID_REFRESH = "El token de renovación es inválido o expiró. Inicia sesión de nuevo"


def _refresh_unauthorized() -> HTTPException:
    return ApiError("invalid_refresh_token", 
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=INVALID_REFRESH,
        headers={"WWW-Authenticate": "Bearer"},
    )


def issue_refresh_token(
    db: Session, user: User, family_id: uuid.UUID | None = None, audience: str = "portal"
) -> tuple[str, uuid.UUID]:
    """Create a refresh token (new family unless `family_id` is given). Returns (raw, family). Caller commits.

    Backoffice sessions are shorter than customer sessions.
    """
    lifetime = (
        timedelta(hours=settings.backoffice_refresh_token_hours) if audience == "backoffice"
        else timedelta(days=settings.refresh_token_days)
    )
    raw = secrets.token_urlsafe(48)
    family = family_id or uuid.uuid4()
    db.add(RefreshToken(
        user_id=user.id,
        family_id=family,
        audience=audience,
        token_hash=_hash(raw),
        expires_at=datetime.now(timezone.utc) + lifetime,
    ))
    db.flush()
    return raw, family


def revoke_family(db: Session, family_id: uuid.UUID) -> None:
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc))
        .execution_options(synchronize_session=False)
    )


def revoke_all_sessions(db: Session, user: User) -> None:
    """Kill every session of the user: refresh tokens now, access tokens via token_version."""
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc))
        .execution_options(synchronize_session=False)
    )
    user.token_version += 1


def rotate_refresh_token(
    db: Session, raw: str, audience: str = "portal", user_type: str | None = None
) -> tuple[User, str, uuid.UUID]:
    """Exchange a refresh token for a new one in the same family. Raises 401 otherwise.

    A token only renews the audience it was issued for: a backoffice token presented at the portal
    (or the reverse) is refused as if it did not exist.

    Reusing a token that was already rotated (or revoked) means it leaked, so the whole
    family is revoked. That revocation is committed here, before the error is raised.
    """
    row = db.scalars(
        select(RefreshToken).where(RefreshToken.token_hash == _hash(raw)).with_for_update()
    ).first()
    if row is None or row.audience != audience:
        raise _refresh_unauthorized()

    now = datetime.now(timezone.utc)
    if row.used_at is not None or row.revoked_at is not None:
        # A token that was already rotated coming back means someone kept a copy. A merely revoked
        # one (after logout, password change) is an ordinary stale client.
        stolen = row.used_at is not None
        audit.record(
            db, Action.REFRESH_REUSE_DETECTED if stolen else Action.REFRESH_DENIED, outcome=FAILURE,
            target_type="user", target_id=row.user_id,
            details={"family_id": str(row.family_id), "reason": "rotated_token_reused" if stolen else "revoked"})
        revoke_family(db, row.family_id)
        db.commit()
        raise _refresh_unauthorized()
    if row.expires_at <= now:
        raise _refresh_unauthorized()

    user = db.get(User, row.user_id)
    if (
        user is None
        or not user.is_active
        or (user_type is not None and user.user_type_code != user_type)
        or (user.user_type_code == "recycler" and user.verification_status != VerificationStatus.verified)
    ):
        audit.record(db, Action.REFRESH_DENIED, outcome=FAILURE, target_type="user", target_id=row.user_id,
                     details={"family_id": str(row.family_id), "reason": "account_inactive_or_unverified"})
        revoke_family(db, row.family_id)
        db.commit()
        raise _refresh_unauthorized()

    row.used_at = now
    new_raw, family = issue_refresh_token(db, user, row.family_id, audience)
    return user, new_raw, family


def build_token_response(user: User, refresh_token: str, family_id: uuid.UUID) -> dict:
    access = create_access_token({
        "sub": str(user.id),
        "user_type": user.user_type_code,
        "role": user.role_code,
        "fid": str(family_id),
        "tv": user.token_version,
    })
    return {
        "access_token": access,
        "token_type": "bearer",
        "expires_in": settings.access_token_expire_minutes * 60,
        "refresh_token": refresh_token,
    }


# --- Account flows (each one is a unit of work: it commits) ---------------------------
# Routers only translate HTTP to these calls. Where an email must go out, the flow returns the
# token so the router can queue the message as a background task after the response.

# Verified against when the account does not exist, so every failure costs one bcrypt check.
DUMMY_PASSWORD_HASH = hash_password(secrets.token_hex(16))


def revoke_session(db: Session, payload: dict) -> None:
    """Close the session an access token belongs to: the token itself and its refresh family. Caller commits."""
    db.add(RevokedToken(
        jti=payload["jti"],
        expires_at=datetime.fromtimestamp(payload["exp"], tz=timezone.utc),
    ))
    if payload.get("fid"):
        revoke_family(db, uuid.UUID(payload["fid"]))


def logout(db: Session, actor: User, payload: dict) -> None:
    revoke_session(db, payload)
    audit.record(db, Action.LOGOUT, actor=actor, target_type="user", target_id=actor.id)
    db.commit()


def register_user(db: Session, data: dict, actor: User | None) -> tuple[User, str | None]:
    """Create an account. Returns the user and the email-verification token to send (if any)."""
    role_code = data.get("role_code")
    if role_code is not None:
        # Roles are handed out by an organization admin, never self-assigned.
        ensure_can_assign_role(actor, role_code, data["user_type_code"])
        role = db.scalars(select(Role).where(Role.code == role_code, Role.is_active.is_(True))).first()
        if role is None:
            raise ApiError(
                "invalid_role", status_code=422,
                detail=f"role_code '{role_code}' no es válido o está inactivo")

    # Staff created by an organization's admin belong to that organization. The client never says which:
    # it is not part of the request, so it cannot be chosen (or forged) from outside.
    if actor is not None and has_role(actor, ORG_ADMINS) and data["user_type_code"] == actor.user_type_code:
        data["organization_id"] = actor.organization_id

    if data.get("user_type_code") == "recycler":
        data.pop("password", None)
        data["password_hash"] = ""
        data["verification_status"] = VerificationStatus.pending
    else:
        data["password_hash"] = hash_password(data.pop("password"))

    user = User(**data)
    try:
        db.add(user)
        db.flush()
        # Recyclers confirm their email when they activate the account; everyone else confirms now.
        verification_token = (
            None if user.user_type_code == "recycler"
            else issue_token(db, user, VERIFY_EMAIL)
        )
        audit.record(
            db, Action.USER_REGISTERED, actor=actor, target_type="user", target_id=user.id,
            details={"user_type": user.user_type_code, "role_code": user.role_code})
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise ApiError(
            "account_already_exists", status_code=status.HTTP_409_CONFLICT,
            detail="Email or ID number already registered")
    return user, verification_token


def login(db: Session, email: str, password: str) -> dict:
    """Check credentials and open a session. Returns the token response."""
    user = db.scalars(select(User).where(func.lower(User.email) == email.strip().lower())).first()

    # Always run one bcrypt comparison, whether the account exists, is locked or has no
    # password yet, so response time does not reveal which case it is.
    locked = user is not None and is_locked(user)
    # Somos R's own accounts never sign in through this endpoint: they use the backoffice, with a
    # second factor. Answering like a wrong password keeps their existence private, and not counting
    # failures here means nobody can lock an administrator out by guessing at the public endpoint.
    platform = user is not None and user.user_type_code == "platform"
    real_hash = user.password_hash if user is not None and not locked and not platform else ""
    password_ok = verify_password(password, real_hash or DUMMY_PASSWORD_HASH) and bool(real_hash)

    if user is None or locked or platform or not password_ok:
        if user is not None and not locked and not platform and user.password_hash:
            register_failed_login(user)
        # The client only ever sees one answer; the trail records the real reason. The attempted
        # email is not stored: it is attacker-controlled text and, for typos, someone else's data.
        reason = (
            "unknown_account" if user is None else "platform_account" if platform
            else "locked" if locked else "no_password" if not user.password_hash else "bad_password"
        )
        audit.record(
            db, Action.LOGIN_FAILED, outcome=FAILURE, target_type="user" if user else None,
            target_id=user.id if user else None, details={"reason": reason})
        db.commit()  # persist the failure even though the request is about to fail
        # One answer for unknown account, wrong password and temporary lock.
        metrics.LOGINS.labels("failed").inc()
        raise ApiError(
            "invalid_credentials", status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials", headers={"WWW-Authenticate": "Bearer"})

    if user.failed_login_attempts or user.locked_until:
        clear_login_failures(user)
        db.commit()

    if not user.is_active:
        audit.record(db, Action.LOGIN_FAILED, outcome=FAILURE, target_type="user",
                     target_id=user.id, details={"reason": "inactive"})
        db.commit()
        metrics.LOGINS.labels("blocked").inc()
        raise ApiError("account_disabled", status_code=status.HTTP_403_FORBIDDEN,
                       detail="Tu cuenta está desactivada")

    if user.user_type_code == "recycler" and user.verification_status != VerificationStatus.verified:
        audit.record(db, Action.LOGIN_FAILED, outcome=FAILURE, target_type="user",
                     target_id=user.id, details={"reason": "pending_verification"})
        db.commit()
        metrics.LOGINS.labels("blocked").inc()
        raise ApiError(
            "account_not_verified", status_code=status.HTTP_403_FORBIDDEN,
            detail="Tu cuenta está pendiente de verificación o fue rechazada")

    refresh_token, family_id = issue_refresh_token(db, user)
    audit.record(db, Action.LOGIN, actor=user, target_type="user", target_id=user.id)
    db.commit()
    metrics.LOGINS.labels("success").inc()
    return build_token_response(user, refresh_token, family_id)


def refresh_session(db: Session, raw_refresh_token: str) -> dict:
    user, refresh_token, family_id = rotate_refresh_token(db, raw_refresh_token)
    db.commit()
    return build_token_response(user, refresh_token, family_id)


def activate_account(db: Session, token: str, password: str) -> None:
    user = consume_token(db, token, ACTIVATE)
    if user.user_type_code != "recycler" or user.password_hash:
        raise ApiError("invalid_link", status_code=status.HTTP_400_BAD_REQUEST, detail=INVALID_LINK)

    user.password_hash = hash_password(password)
    user.email_verified_at = datetime.now(timezone.utc)
    clear_login_failures(user)
    revoke_all_sessions(db, user)
    audit.record(db, Action.ACCOUNT_ACTIVATED, actor=user, target_type="user", target_id=user.id)
    db.commit()


def verify_email(db: Session, token: str) -> None:
    user = consume_token(db, token, VERIFY_EMAIL)
    if user.email_verified_at is None:
        user.email_verified_at = datetime.now(timezone.utc)
    audit.record(db, Action.EMAIL_VERIFIED, actor=user, target_type="user", target_id=user.id)
    db.commit()


def resend_verification(db: Session, user: User) -> str | None:
    """A fresh verification token, or None when the email is already confirmed."""
    if user.email_verified_at is not None:
        return None
    token = issue_token(db, user, VERIFY_EMAIL)
    db.commit()
    return token


def forgot_password(db: Session, email: str) -> tuple[User, str] | None:
    """A reset token for the account, or None. The caller answers the same either way."""
    user = db.scalars(select(User).where(func.lower(User.email) == email)).first()
    # Pending recyclers (no password yet) get nothing, and neither do Somos R's own accounts: their
    # credentials are never recoverable through a public email link.
    if user is None or not user.is_active or not user.password_hash or user.user_type_code == "platform":
        return None
    token = issue_token(db, user, RESET_PASSWORD)
    audit.record(db, Action.PASSWORD_RESET_REQUESTED, target_type="user", target_id=user.id)
    db.commit()
    return user, token


def reset_password(db: Session, token: str, password: str) -> None:
    user = consume_token(db, token, RESET_PASSWORD)
    user.password_hash = hash_password(password)
    clear_login_failures(user)
    revoke_all_sessions(db, user)
    if user.email_verified_at is None:
        user.email_verified_at = datetime.now(timezone.utc)  # they proved control of the inbox
    audit.record(db, Action.PASSWORD_RESET, actor=user, target_type="user", target_id=user.id)
    db.commit()


def change_password(db: Session, user: User, current_password: str, new_password: str) -> None:
    if not verify_password(current_password, user.password_hash):
        # Someone holding a live session guessing the current password is worth knowing about.
        audit.record(db, Action.PASSWORD_CHANGE_FAILED, actor=user, outcome=FAILURE,
                     target_type="user", target_id=user.id)
        db.commit()
        raise ApiError("wrong_current_password", status_code=status.HTTP_400_BAD_REQUEST,
                       detail="La contraseña actual es incorrecta")

    user.password_hash = hash_password(new_password)
    # Every session, including this one, must sign in again with the new password.
    revoke_all_sessions(db, user)
    audit.record(db, Action.PASSWORD_CHANGED, actor=user, target_type="user", target_id=user.id)
    db.commit()
