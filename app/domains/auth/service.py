"""One-time tokens (activation, email verification, password reset) and their emails."""
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.email import send_email
from app.core.security import create_access_token
from app.domains.audit import service as audit
from app.domains.audit.actions import FAILURE, Action
from app.domains.auth.models import OneTimeToken, RefreshToken
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
    db.query(OneTimeToken).filter(
        OneTimeToken.user_id == user.id,
        OneTimeToken.purpose == purpose,
        OneTimeToken.used_at.is_(None),
    ).update({OneTimeToken.used_at: now}, synchronize_session=False)

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
    invalid = HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=INVALID_LINK)

    row = (
        db.query(OneTimeToken)
        .filter(OneTimeToken.token_hash == _hash(raw), OneTimeToken.purpose == purpose)
        .with_for_update()
        .first()
    )
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
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=INVALID_REFRESH,
        headers={"WWW-Authenticate": "Bearer"},
    )


def issue_refresh_token(db: Session, user: User, family_id: uuid.UUID | None = None) -> tuple[str, uuid.UUID]:
    """Create a refresh token (new family unless `family_id` is given). Returns (raw, family). Caller commits."""
    raw = secrets.token_urlsafe(48)
    family = family_id or uuid.uuid4()
    db.add(RefreshToken(
        user_id=user.id,
        family_id=family,
        token_hash=_hash(raw),
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_days),
    ))
    db.flush()
    return raw, family


def revoke_family(db: Session, family_id: uuid.UUID) -> None:
    db.query(RefreshToken).filter(
        RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None)
    ).update({RefreshToken.revoked_at: datetime.now(timezone.utc)}, synchronize_session=False)


def revoke_all_sessions(db: Session, user: User) -> None:
    """Kill every session of the user: refresh tokens now, access tokens via token_version."""
    db.query(RefreshToken).filter(
        RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)
    ).update({RefreshToken.revoked_at: datetime.now(timezone.utc)}, synchronize_session=False)
    user.token_version += 1


def rotate_refresh_token(db: Session, raw: str) -> tuple[User, str, uuid.UUID]:
    """Exchange a refresh token for a new one in the same family. Raises 401 otherwise.

    Reusing a token that was already rotated (or revoked) means it leaked, so the whole
    family is revoked. That revocation is committed here, before the error is raised.
    """
    row = (
        db.query(RefreshToken)
        .filter(RefreshToken.token_hash == _hash(raw))
        .with_for_update()
        .first()
    )
    if row is None:
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
        or (user.user_type_code == "recycler" and user.verification_status != VerificationStatus.verified)
    ):
        audit.record(db, Action.REFRESH_DENIED, outcome=FAILURE, target_type="user", target_id=row.user_id,
                     details={"family_id": str(row.family_id), "reason": "account_inactive_or_unverified"})
        revoke_family(db, row.family_id)
        db.commit()
        raise _refresh_unauthorized()

    row.used_at = now
    new_raw, family = issue_refresh_token(db, user, row.family_id)
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
