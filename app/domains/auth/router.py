
from fastapi import APIRouter, BackgroundTasks, Depends, Request, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.permissions import capabilities_for
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
)
from app.domains.auth import service as auth_service
from app.domains.auth.docs import (
    ACTIVATE_DOCS,
    CHANGE_PASSWORD_DOCS,
    FORGOT_PASSWORD_DOCS,
    LOGIN_DOCS,
    LOGOUT_DOCS,
    ME_DOCS,
    REFRESH_DOCS,
    REGISTER_DOCS,
    RESEND_VERIFICATION_DOCS,
    RESET_PASSWORD_DOCS,
    VERIFY_EMAIL_DOCS,
)
from app.domains.auth.schemas import (
    ActivateRequest,
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    MeResponse,
    MessageResponse,
    RefreshRequest,
    RegisterRequest,
    ResetPasswordRequest,
    TokenRequest,
    TokenResponse,
    UserResponse,
)
from app.domains.users.models import User
from app.domains.users.schemas import UserDetailResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me", response_model=MeResponse, **ME_DOCS)
def me(actor: User = Depends(get_current_user)):
    data = UserDetailResponse.model_validate(actor).model_dump()
    return MeResponse(**data, capabilities=capabilities_for(actor))


@router.post("/logout", status_code=status.HTTP_200_OK, **LOGOUT_DOCS)
def logout(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    auth_service.logout(db, actor, decode_access_token(credentials.credentials))
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
    user, verification_token = auth_service.register_user(db, body.model_dump(), actor)
    if verification_token:
        background_tasks.add_task(
            auth_service.send_verification_email, user.email, user.full_name, verification_token)
    return user


@router.post("/login", response_model=TokenResponse, **LOGIN_DOCS)
@limiter.limit(login_limit)
def login(request: Request, body: LoginRequest, db: Session = Depends(get_db)):
    return TokenResponse(**auth_service.login(db, body.email, body.password))


@router.post("/refresh", response_model=TokenResponse, **REFRESH_DOCS)
@limiter.limit(token_flow_limit)
def refresh(request: Request, body: RefreshRequest, db: Session = Depends(get_db)):
    return TokenResponse(**auth_service.refresh_session(db, body.refresh_token))


@router.post("/activate", response_model=MessageResponse, **ACTIVATE_DOCS)
@limiter.limit(token_flow_limit)
def activate_account(request: Request, body: ActivateRequest, db: Session = Depends(get_db)):
    auth_service.activate_account(db, body.token, body.password)
    return MessageResponse(message="Cuenta activada. Ya puedes iniciar sesión")


@router.post("/verify-email", response_model=MessageResponse, **VERIFY_EMAIL_DOCS)
@limiter.limit(token_flow_limit)
def verify_email(request: Request, body: TokenRequest, db: Session = Depends(get_db)):
    auth_service.verify_email(db, body.token)
    return MessageResponse(message="Correo confirmado")


@router.post("/resend-verification", response_model=MessageResponse, **RESEND_VERIFICATION_DOCS)
def resend_verification(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    token = auth_service.resend_verification(db, user)
    if token:
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
    # Same answer whether or not the account exists.
    reset = auth_service.forgot_password(db, body.email)
    if reset is not None:
        user, token = reset
        background_tasks.add_task(
            auth_service.send_password_reset_email, user.email, user.full_name, token)
    return MessageResponse(
        message="Si el correo está registrado, te enviamos un enlace para restablecer la contraseña")


@router.post("/reset-password", response_model=MessageResponse, **RESET_PASSWORD_DOCS)
@limiter.limit(token_flow_limit)
def reset_password(request: Request, body: ResetPasswordRequest, db: Session = Depends(get_db)):
    auth_service.reset_password(db, body.token, body.password)
    return MessageResponse(message="Contraseña actualizada. Ya puedes iniciar sesión")


@router.post("/change-password", response_model=MessageResponse, **CHANGE_PASSWORD_DOCS)
@limiter.limit(token_flow_limit)
def change_password(
    request: Request,
    body: ChangePasswordRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    auth_service.change_password(db, user, body.current_password, body.new_password)
    return MessageResponse(message="Contraseña actualizada. Inicia sesión de nuevo")
