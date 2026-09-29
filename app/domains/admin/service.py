"""Sign-in and second factor for Somos R's own accounts (the backoffice).

Sign-in is two steps. The password step answers with a short-lived *challenge* token (audience
`backoffice-mfa`) that proves only "the password was right"; the second step trades it plus a TOTP
code (or a recovery code) for the real session (audience `backoffice`). Nothing of the backoffice
opens without the second factor, and a first-time account must enroll one before it gets in.

Failures at either step count against the same lockout as the public login, and every outcome is
audited. Each function here is a unit of work: it commits.
"""
import uuid
from datetime import datetime, timezone

from fastapi import status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core import mfa
from app.core.config import settings
from app.core.errors import ApiError
from app.core.permissions import PLATFORM_CAPABILITIES, has_capability
from app.core.security import (
    BACKOFFICE,
    BACKOFFICE_MFA,
    create_access_token,
    decode_access_token,
    verify_password,
)
from app.domains.audit import service as audit
from app.domains.audit.actions import FAILURE, Action
from app.domains.auth import service as auth_service
from app.domains.auth.models import MfaCredential, RecoveryCode, RevokedToken
from app.domains.users.models import User

MFA_STATUS_ENROLL = "enrollment_required"
MFA_STATUS_CODE = "code_required"


def _invalid_credentials() -> ApiError:
    return ApiError(
        "invalid_credentials", status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credenciales inválidas", headers={"WWW-Authenticate": "Bearer"})


def _invalid_mfa_code() -> ApiError:
    return ApiError(
        "invalid_mfa_code", status_code=status.HTTP_401_UNAUTHORIZED,
        detail="El código es incorrecto o ya fue usado", headers={"WWW-Authenticate": "Bearer"})


def _mfa_session_expired() -> ApiError:
    return ApiError(
        "mfa_session_expired", status_code=status.HTTP_401_UNAUTHORIZED,
        detail="La verificación expiró. Inicia sesión de nuevo", headers={"WWW-Authenticate": "Bearer"})


def _fail(db: Session, user: User | None, action: str, reason: str) -> None:
    """Record a failed attempt (and count it against the lockout) before the caller raises."""
    if user is not None:
        auth_service.register_failed_login(user)
    audit.record(
        db, action, outcome=FAILURE, target_type="user" if user else None,
        target_id=user.id if user else None, details={"reason": reason})
    db.commit()


# --- Step 1: password ---------------------------------------------------------------

def start_login(db: Session, email: str, password: str) -> dict:
    user = db.scalars(select(User).where(func.lower(User.email) == email.strip().lower())).first()

    # One bcrypt comparison whatever the case, so timing does not say which one it is. Only Somos R
    # accounts sign in here; a customer answers exactly like an unknown address.
    account = user if user is not None and user.user_type_code == "platform" else None
    locked = account is not None and auth_service.is_locked(account)
    real_hash = account.password_hash if account is not None and not locked else ""
    password_ok = verify_password(password, real_hash or auth_service.DUMMY_PASSWORD_HASH) and bool(real_hash)

    if account is None or locked or not password_ok or not account.is_active:
        reason = (
            "unknown_account" if user is None else "not_platform" if account is None
            else "locked" if locked else "bad_password" if not password_ok else "inactive"
        )
        _fail(db, None if locked else account, Action.ADMIN_LOGIN_FAILED, reason)
        raise _invalid_credentials()

    credential = db.get(MfaCredential, account.id)
    return {
        "mfa_token": create_access_token(
            {"sub": str(account.id), "tv": account.token_version}, audience=BACKOFFICE_MFA,
            minutes=settings.mfa_challenge_minutes),
        "mfa_status": MFA_STATUS_CODE if credential and credential.enabled_at else MFA_STATUS_ENROLL,
        "expires_in": settings.mfa_challenge_minutes * 60,
    }


# --- Step 2: second factor ----------------------------------------------------------

def _user_from_challenge(db: Session, mfa_token: str) -> tuple[User, dict]:
    """The user a valid, unspent challenge token belongs to."""
    try:
        payload = decode_access_token(mfa_token, BACKOFFICE_MFA)
        user_id = uuid.UUID(payload["sub"])
    except (ApiError, ValueError, TypeError):
        raise _mfa_session_expired()
    if db.get(RevokedToken, payload["jti"]) is not None:
        raise _mfa_session_expired()
    user = db.get(User, user_id)
    if (
        user is None or user.user_type_code != "platform" or not user.is_active
        or payload.get("tv", 0) != user.token_version
    ):
        raise _mfa_session_expired()
    if auth_service.is_locked(user):
        raise _invalid_credentials()
    return user, payload


def _spend_challenge(db: Session, payload: dict) -> None:
    db.add(RevokedToken(
        jti=payload["jti"], expires_at=datetime.fromtimestamp(payload["exp"], tz=timezone.utc)))


def _open_session(db: Session, user: User) -> dict:
    refresh_token, family_id = auth_service.issue_refresh_token(db, user, audience="backoffice")
    minutes = settings.backoffice_access_token_minutes
    access = create_access_token(
        {"sub": str(user.id), "user_type": user.user_type_code, "role": user.role_code,
         "fid": str(family_id), "tv": user.token_version},
        audience=BACKOFFICE, minutes=minutes)
    return {"access_token": access, "token_type": "bearer", "expires_in": minutes * 60,
            "refresh_token": refresh_token}


def _new_recovery_codes(db: Session, user: User) -> list[str]:
    db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    codes = mfa.generate_recovery_codes()
    db.add_all(RecoveryCode(user_id=user.id, code_hash=mfa.hash_recovery_code(c)) for c in codes)
    return codes


def _recovery_codes_remaining(db: Session, user: User) -> int:
    return db.scalar(
        select(func.count(RecoveryCode.id)).where(RecoveryCode.user_id == user.id, RecoveryCode.used_at.is_(None))
    ) or 0


def begin_enrollment(db: Session, mfa_token: str) -> dict:
    user, _ = _user_from_challenge(db, mfa_token)
    credential = db.get(MfaCredential, user.id)
    if credential is not None and credential.enabled_at is not None:
        raise ApiError("mfa_already_enrolled", status_code=status.HTTP_409_CONFLICT,
                       detail="Esta cuenta ya tiene un segundo factor")
    secret = mfa.generate_secret()
    if credential is None:
        db.add(MfaCredential(user_id=user.id, secret_encrypted=mfa.encrypt_secret(secret)))
    else:  # an unfinished enrollment: start over with a fresh secret
        credential.secret_encrypted = mfa.encrypt_secret(secret)
        credential.last_step = None
    db.commit()
    return {"secret": secret, "otpauth_uri": mfa.provisioning_uri(secret, user.email)}


def confirm_enrollment(db: Session, mfa_token: str, code: str) -> dict:
    user, payload = _user_from_challenge(db, mfa_token)
    credential = db.get(MfaCredential, user.id)
    if credential is None or credential.enabled_at is not None:
        raise ApiError("mfa_not_started", status_code=status.HTTP_400_BAD_REQUEST,
                       detail="Primero solicita el secreto del segundo factor")
    secret = mfa.decrypt_secret(credential.secret_encrypted)
    step = mfa.verify_totp(secret, code) if secret else None
    if step is None:
        _fail(db, user, Action.ADMIN_MFA_FAILED, "bad_code_at_enrollment")
        raise _invalid_mfa_code()

    credential.enabled_at = datetime.now(timezone.utc)
    credential.last_step = step
    codes = _new_recovery_codes(db, user)
    auth_service.clear_login_failures(user)
    _spend_challenge(db, payload)
    session = _open_session(db, user)
    audit.record(db, Action.ADMIN_MFA_ENROLLED, actor=user, target_type="user", target_id=user.id)
    audit.record(db, Action.ADMIN_LOGIN, actor=user, target_type="user", target_id=user.id,
                 details={"method": "enrollment"})
    db.commit()
    return {**session, "recovery_codes": codes}


def verify_login(db: Session, mfa_token: str, code: str | None, recovery_code: str | None) -> dict:
    user, payload = _user_from_challenge(db, mfa_token)
    credential = db.get(MfaCredential, user.id)
    if credential is None or credential.enabled_at is None:
        raise ApiError("mfa_not_enrolled", status_code=status.HTTP_400_BAD_REQUEST,
                       detail="Esta cuenta aún no tiene un segundo factor")

    method = "totp" if code is not None else "recovery"
    if code is not None:
        secret = mfa.decrypt_secret(credential.secret_encrypted)
        step = mfa.verify_totp(secret, code, credential.last_step) if secret else None
        if step is None:
            _fail(db, user, Action.ADMIN_MFA_FAILED, "bad_code")
            raise _invalid_mfa_code()
        credential.last_step = step
    else:
        row = db.scalars(select(RecoveryCode).where(
            RecoveryCode.user_id == user.id, RecoveryCode.code_hash == mfa.hash_recovery_code(recovery_code or ""),
            RecoveryCode.used_at.is_(None)).with_for_update()).first()
        if row is None:
            _fail(db, user, Action.ADMIN_MFA_FAILED, "bad_recovery_code")
            raise _invalid_mfa_code()
        row.used_at = datetime.now(timezone.utc)
        audit.record(db, Action.ADMIN_RECOVERY_CODE_USED, actor=user, target_type="user", target_id=user.id)

    auth_service.clear_login_failures(user)
    _spend_challenge(db, payload)
    session = _open_session(db, user)
    audit.record(db, Action.ADMIN_LOGIN, actor=user, target_type="user", target_id=user.id,
                 details={"method": method})
    db.commit()
    if method == "recovery":
        session["recovery_codes_remaining"] = _recovery_codes_remaining(db, user)
    return session


# --- Session ------------------------------------------------------------------------

def refresh_session(db: Session, raw_refresh_token: str) -> dict:
    user, new_refresh, family_id = auth_service.rotate_refresh_token(
        db, raw_refresh_token, "backoffice", user_type="platform")
    minutes = settings.backoffice_access_token_minutes
    access = create_access_token(
        {"sub": str(user.id), "user_type": user.user_type_code, "role": user.role_code,
         "fid": str(family_id), "tv": user.token_version},
        audience=BACKOFFICE, minutes=minutes)
    db.commit()
    return {"access_token": access, "token_type": "bearer", "expires_in": minutes * 60,
            "refresh_token": new_refresh}


def logout(db: Session, actor: User, payload: dict) -> None:
    auth_service.revoke_session(db, payload)
    audit.record(db, Action.ADMIN_LOGOUT, actor=actor, target_type="user", target_id=actor.id)
    db.commit()


# --- Self-service and management of the second factor -------------------------------

def profile(db: Session, user: User) -> dict:
    credential = db.get(MfaCredential, user.id)
    return {
        "id": user.id, "email": user.email, "full_name": user.full_name,
        "user_type_code": user.user_type_code, "role_code": user.role_code,
        "capabilities": sorted(c for c in PLATFORM_CAPABILITIES if has_capability(user, c)),
        "mfa_enabled": bool(credential and credential.enabled_at),
        "recovery_codes_remaining": _recovery_codes_remaining(db, user),
    }


def regenerate_recovery_codes(db: Session, user: User, code: str) -> list[str]:
    """New recovery codes (the old ones stop working). Needs a fresh authenticator code."""
    credential = db.get(MfaCredential, user.id)
    secret = mfa.decrypt_secret(credential.secret_encrypted) if credential and credential.enabled_at else None
    step = mfa.verify_totp(secret, code, credential.last_step) if secret and credential else None
    if step is None or credential is None:
        _fail(db, user, Action.ADMIN_MFA_FAILED, "bad_code_regenerating_recovery_codes")
        raise _invalid_mfa_code()
    credential.last_step = step
    codes = _new_recovery_codes(db, user)
    audit.record(db, Action.ADMIN_RECOVERY_CODES_REGENERATED, actor=user, target_type="user", target_id=user.id)
    db.commit()
    return codes


def reset_mfa(db: Session, actor: User, target_id: uuid.UUID) -> None:
    """Take away another Somos R account's second factor (lost device and lost recovery codes).

    The target must sign in again and enroll a new one; all their sessions end now.
    """
    if target_id == actor.id:
        raise ApiError("cannot_reset_own_mfa", status_code=status.HTTP_403_FORBIDDEN,
                       detail="No puedes restablecer tu propio segundo factor: pídeselo a otra persona de Somos R")
    target = db.get(User, target_id)
    if target is None or target.user_type_code != "platform":
        raise ApiError("user_not_found", status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado")
    db.execute(delete(MfaCredential).where(MfaCredential.user_id == target.id))
    db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == target.id))
    auth_service.revoke_all_sessions(db, target)
    audit.record(db, Action.ADMIN_MFA_RESET, actor=actor, target_type="user", target_id=target.id)
    db.commit()

