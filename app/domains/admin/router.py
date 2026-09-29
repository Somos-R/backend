import uuid

from fastapi import APIRouter, Depends, Request, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.network import enforce_admin_network
from app.core.rate_limit import admin_auth_limit, admin_limit, limiter
from app.core.security import (
    BACKOFFICE,
    bearer_scheme,
    decode_access_token,
    get_platform_user,
    require_capability,
)
from app.domains.admin import service
from app.domains.admin.schemas import (
    AdminMeResponse,
    BackofficeSessionResponse,
    MfaChallengeResponse,
    MfaEnrollConfirmRequest,
    MfaEnrollStartResponse,
    MfaTokenRequest,
    MfaVerifyRequest,
    RecoveryCodesRequest,
    RecoveryCodesResponse,
)
from app.domains.auth.schemas import LoginRequest, MessageResponse, RefreshRequest
from app.domains.users.models import User

# Everything under /admin: reachable only from the allowed networks, and with stricter limits.
router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(enforce_admin_network)])


@router.post("/auth/login", response_model=MfaChallengeResponse)
@limiter.limit(admin_auth_limit)
def login(request: Request, body: LoginRequest, db: Session = Depends(get_db)):
    """Step 1: password. Answers with a short-lived token to present with the second factor."""
    return service.start_login(db, body.email, body.password)


@router.post("/auth/mfa/enroll", response_model=MfaEnrollStartResponse)
@limiter.limit(admin_auth_limit)
def mfa_enroll(request: Request, body: MfaTokenRequest, db: Session = Depends(get_db)):
    """First sign-in only: the secret to load into an authenticator app."""
    return service.begin_enrollment(db, body.mfa_token)


@router.post("/auth/mfa/enroll/confirm", response_model=BackofficeSessionResponse)
@limiter.limit(admin_auth_limit)
def mfa_enroll_confirm(request: Request, body: MfaEnrollConfirmRequest, db: Session = Depends(get_db)):
    """Proves the authenticator works, turns the second factor on, opens the session and hands out the recovery codes."""
    return service.confirm_enrollment(db, body.mfa_token, body.code)


@router.post("/auth/mfa/verify", response_model=BackofficeSessionResponse)
@limiter.limit(admin_auth_limit)
def mfa_verify(request: Request, body: MfaVerifyRequest, db: Session = Depends(get_db)):
    """Step 2: authenticator code or one recovery code. Opens the session."""
    return service.verify_login(db, body.mfa_token, body.code, body.recovery_code)


@router.post("/auth/refresh", response_model=BackofficeSessionResponse)
@limiter.limit(admin_limit)
def refresh(request: Request, body: RefreshRequest, db: Session = Depends(get_db)):
    return service.refresh_session(db, body.refresh_token)


@router.post("/auth/logout", response_model=MessageResponse, status_code=status.HTTP_200_OK)
@limiter.limit(admin_limit)
def logout(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
    actor: User = Depends(get_platform_user),
):
    service.logout(db, actor, decode_access_token(credentials.credentials, BACKOFFICE))
    return MessageResponse(message="Sesión cerrada")


@router.get("/me", response_model=AdminMeResponse)
@limiter.limit(admin_limit)
def me(request: Request, db: Session = Depends(get_db), actor: User = Depends(get_platform_user)):
    """The signed-in Somos R account with the capabilities of its role."""
    return service.profile(db, actor)


@router.post("/auth/mfa/recovery-codes", response_model=RecoveryCodesResponse)
@limiter.limit(admin_auth_limit)
def regenerate_recovery_codes(
    request: Request,
    body: RecoveryCodesRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(get_platform_user),
):
    """New recovery codes (the old ones stop working). Needs a fresh authenticator code."""
    return RecoveryCodesResponse(recovery_codes=service.regenerate_recovery_codes(db, actor, body.code))


@router.post("/users/{user_id}/mfa/reset", response_model=MessageResponse)
@limiter.limit(admin_limit)
def reset_mfa(
    request: Request,
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    actor: User = Depends(require_capability("users.manage")),
):
    """Take another Somos R account's second factor away (lost device and codes); they enroll a new one."""
    service.reset_mfa(db, actor, user_id)
    return MessageResponse(message="Segundo factor restablecido. La persona deberá configurarlo de nuevo")
