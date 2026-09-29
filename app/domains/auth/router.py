import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Request, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import metrics
from app.core.database import get_db
from app.core.errors import ApiError
from app.core.permissions import ensure_can_assign_role
from app.core.rate_limit import (
    forgot_password_limit,
    limiter,
    login_limit,
    register_limit,
    token_flow_limit,
)
from app.core.security import (
    bearer_scheme,
    decode_access_token,
    get_current_user,
    get_optional_user,
    hash_password,
    verify_password,
)
from app.domains.audit import service as audit
from app.domains.audit.actions import FAILURE, Action
from app.domains.auth import service as auth_service
from app.domains.auth.docs import (
    ACTIVATE_DOCS,
    CHANGE_PASSWORD_DOCS,
    FORGOT_PASSWORD_DOCS,
    LOGIN_DOCS,
    LOGOUT_DOCS,
    REFRESH_DOCS,
    REGISTER_DOCS,
    RESEND_VERIFICATION_DOCS,
    RESET_PASSWORD_DOCS,
    VERIFY_EMAIL_DOCS,
)
from app.domains.auth.models import RevokedToken
from app.domains.auth.schemas import (
    ActivateRequest,
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    RefreshRequest,
    RegisterRequest,
    ResetPasswordRequest,
    TokenRequest,
    TokenResponse,
    UserResponse,
)
from app.domains.catalogs.models import Role
from app.domains.users.enums import VerificationStatus
from app.domains.users.models import User

router = APIRouter(prefix="/auth", tags=["auth"])

# Verified against when the account does not exist, so every failure costs one bcrypt check.
DUMMY_PASSWORD_HASH = hash_password(secrets.token_hex(16))


@router.post("/logout", status_code=status.HTTP_200_OK, **LOGOUT_DOCS)
def logout(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    payload = decode_access_token(credentials.credentials)
    db.add(RevokedToken(
        jti=payload["jti"],
        expires_at=datetime.fromtimestamp(payload["exp"], tz=timezone.utc),
    ))
    if payload.get("fid"):
        auth_service.revoke_family(db, uuid.UUID(payload["fid"]))
    audit.record(db, Action.LOGOUT, actor=actor, target_type="user", target_id=actor.id)
    db.commit()
    return {"message": "Sesión cerrada exitosamente"}


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED, **REGISTER_DOCS)
@limiter.limit(register_limit)
def register(
    request: Request,
    
    body: RegisterRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    actor: User | None = Depends(get_optional_user),
):
    data = body.model_dump()

    role_code = data.get("role_code")
    if role_code is not None:
        # Roles are handed out by an organization admin, never self-assigned.
        ensure_can_assign_role(actor, role_code, data["user_type_code"])
        role = db.query(Role).filter(Role.code == role_code, Role.is_active == True).first()
        if role is None:
            raise ApiError("invalid_role", 
                status_code=422,
                detail=f"role_code '{role_code}' no es válido o está inactivo",
            )

    if data.get("user_type_code") == "recycler":
        data.pop("password", None)
        data["password_hash"] = ""
        data["verification_status"] = VerificationStatus.pending
    else:
        plain_password = data.pop("password")
        data["password_hash"] = hash_password(plain_password)

    user = User(**data)

    try:
        db.add(user)
        db.flush()
        # Recyclers confirm their email when they activate the account; everyone else confirms now.
        verification_token = (
            None if user.user_type_code == "recycler"
            else auth_service.issue_token(db, user, auth_service.VERIFY_EMAIL)
        )
        audit.record(
            db, Action.USER_REGISTERED, actor=actor, target_type="user", target_id=user.id,
            details={"user_type": user.user_type_code, "role_code": user.role_code})
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise ApiError("account_already_exists", 
            status_code=status.HTTP_409_CONFLICT,
            detail="Email or ID number already registered",
        )

    if verification_token:
        background_tasks.add_task(
            auth_service.send_verification_email, user.email, user.full_name, verification_token)
    return user


@router.post("/login", response_model=TokenResponse, **LOGIN_DOCS)
@limiter.limit(login_limit)
def login(request: Request, body: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(func.lower(User.email) == body.email.strip().lower()).first()

    # Always run one bcrypt comparison, whether the account exists, is locked or has no
    # password yet, so response time does not reveal which case it is.
    locked = user is not None and auth_service.is_locked(user)
    real_hash = user.password_hash if user is not None and not locked else ""
    password_ok = verify_password(body.password, real_hash or DUMMY_PASSWORD_HASH) and bool(real_hash)

    if user is None or locked or not password_ok:
        if user is not None and not locked and user.password_hash:
            auth_service.register_failed_login(user)
        # The client only ever sees one answer; the trail records the real reason. The attempted
        # email is not stored: it is attacker-controlled text and, for typos, someone else's data.
        reason = (
            "unknown_account" if user is None else "locked" if locked
            else "no_password" if not user.password_hash else "bad_password"
        )
        audit.record(
            db, Action.LOGIN_FAILED, outcome=FAILURE, target_type="user" if user else None,
            target_id=user.id if user else None, details={"reason": reason})
        db.commit()  # persist the failure even though the request is about to fail
        # One answer for unknown account, wrong password and temporary lock.
        metrics.LOGINS.labels("failed").inc()
        raise ApiError("invalid_credentials", 
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if user.failed_login_attempts or user.locked_until:
        auth_service.clear_login_failures(user)
        db.commit()

    if not user.is_active:
        audit.record(db, Action.LOGIN_FAILED, outcome=FAILURE, target_type="user",
                     target_id=user.id, details={"reason": "inactive"})
        db.commit()
        metrics.LOGINS.labels("blocked").inc()
        raise ApiError("account_disabled", status_code=status.HTTP_403_FORBIDDEN, detail="Tu cuenta está desactivada")

    if user.user_type_code == "recycler" and user.verification_status != VerificationStatus.verified:
        audit.record(db, Action.LOGIN_FAILED, outcome=FAILURE, target_type="user",
                     target_id=user.id, details={"reason": "pending_verification"})
        db.commit()
        metrics.LOGINS.labels("blocked").inc()
        raise ApiError("account_not_verified", 
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tu cuenta está pendiente de verificación o fue rechazada",
        )

    refresh_token, family_id = auth_service.issue_refresh_token(db, user)
    audit.record(db, Action.LOGIN, actor=user, target_type="user", target_id=user.id)
    db.commit()
    metrics.LOGINS.labels("success").inc()
    return TokenResponse(**auth_service.build_token_response(user, refresh_token, family_id))


@router.post("/refresh", response_model=TokenResponse, **REFRESH_DOCS)
@limiter.limit(token_flow_limit)
def refresh(request: Request, body: RefreshRequest, db: Session = Depends(get_db)):
    user, refresh_token, family_id = auth_service.rotate_refresh_token(db, body.refresh_token)
    db.commit()
    return TokenResponse(**auth_service.build_token_response(user, refresh_token, family_id))


@router.post("/activate", response_model=MessageResponse, **ACTIVATE_DOCS)
@limiter.limit(token_flow_limit)
def activate_account(request: Request, body: ActivateRequest, db: Session = Depends(get_db)):
    user = auth_service.consume_token(db, body.token, auth_service.ACTIVATE)
    if user.user_type_code != "recycler" or user.password_hash:
        raise ApiError("invalid_link", status_code=status.HTTP_400_BAD_REQUEST, detail=auth_service.INVALID_LINK)

    user.password_hash = hash_password(body.password)
    user.email_verified_at = datetime.now(timezone.utc)
    auth_service.clear_login_failures(user)
    auth_service.revoke_all_sessions(db, user)
    audit.record(db, Action.ACCOUNT_ACTIVATED, actor=user, target_type="user", target_id=user.id)
    db.commit()
    return MessageResponse(message="Cuenta activada. Ya puedes iniciar sesión")


@router.post("/verify-email", response_model=MessageResponse, **VERIFY_EMAIL_DOCS)
@limiter.limit(token_flow_limit)
def verify_email(request: Request, body: TokenRequest, db: Session = Depends(get_db)):
    user = auth_service.consume_token(db, body.token, auth_service.VERIFY_EMAIL)
    if user.email_verified_at is None:
        user.email_verified_at = datetime.now(timezone.utc)
    audit.record(db, Action.EMAIL_VERIFIED, actor=user, target_type="user", target_id=user.id)
    db.commit()
    return MessageResponse(message="Correo confirmado")


@router.post("/resend-verification", response_model=MessageResponse, **RESEND_VERIFICATION_DOCS)
def resend_verification(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if user.email_verified_at is None:
        token = auth_service.issue_token(db, user, auth_service.VERIFY_EMAIL)
        db.commit()
        background_tasks.add_task(
            auth_service.send_verification_email, user.email, user.full_name, token)
    return MessageResponse(message="Si tu correo no estaba confirmado, te enviamos un nuevo enlace")


@router.post("/forgot-password", response_model=MessageResponse, **FORGOT_PASSWORD_DOCS)
@limiter.limit(forgot_password_limit)
def forgot_password(
    request: Request,
    
    body: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(func.lower(User.email) == body.email).first()
    # Same answer whether or not the account exists; pending recyclers (no password yet) get nothing.
    if user is not None and user.is_active and user.password_hash:
        token = auth_service.issue_token(db, user, auth_service.RESET_PASSWORD)
        audit.record(db, Action.PASSWORD_RESET_REQUESTED, target_type="user", target_id=user.id)
        db.commit()
        background_tasks.add_task(
            auth_service.send_password_reset_email, user.email, user.full_name, token)
    return MessageResponse(
        message="Si el correo está registrado, te enviamos un enlace para restablecer la contraseña")


@router.post("/reset-password", response_model=MessageResponse, **RESET_PASSWORD_DOCS)
@limiter.limit(token_flow_limit)
def reset_password(request: Request, body: ResetPasswordRequest, db: Session = Depends(get_db)):
    user = auth_service.consume_token(db, body.token, auth_service.RESET_PASSWORD)
    user.password_hash = hash_password(body.password)
    auth_service.clear_login_failures(user)
    auth_service.revoke_all_sessions(db, user)
    if user.email_verified_at is None:
        user.email_verified_at = datetime.now(timezone.utc)  # they proved control of the inbox
    audit.record(db, Action.PASSWORD_RESET, actor=user, target_type="user", target_id=user.id)
    db.commit()
    return MessageResponse(message="Contraseña actualizada. Ya puedes iniciar sesión")


@router.post("/change-password", response_model=MessageResponse, **CHANGE_PASSWORD_DOCS)
@limiter.limit(token_flow_limit)
def change_password(
    request: Request,
    
    body: ChangePasswordRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not verify_password(body.current_password, user.password_hash):
        # Someone holding a live session guessing the current password is worth knowing about.
        audit.record(db, Action.PASSWORD_CHANGE_FAILED, actor=user, outcome=FAILURE,
                     target_type="user", target_id=user.id)
        db.commit()
        raise ApiError("wrong_current_password", 
            status_code=status.HTTP_400_BAD_REQUEST, detail="La contraseña actual es incorrecta")

    user.password_hash = hash_password(body.new_password)
    # Every session, including this one, must sign in again with the new password.
    auth_service.revoke_all_sessions(db, user)
    audit.record(db, Action.PASSWORD_CHANGED, actor=user, target_type="user", target_id=user.id)
    db.commit()
    return MessageResponse(message="Contraseña actualizada. Inicia sesión de nuevo")
