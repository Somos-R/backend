from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from jose import jwt
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import (
    bearer_scheme,
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.domains.auth.docs import LOGIN_DOCS, LOGOUT_DOCS, REGISTER_DOCS
from app.domains.auth.models import RevokedToken
from app.domains.auth.schemas import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from app.domains.users.enums import VerificationStatus
from app.domains.users.models import User

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/logout", status_code=status.HTTP_200_OK, **LOGOUT_DOCS)
def logout(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    payload = jwt.decode(
        credentials.credentials,
        settings.secret_key,
        algorithms=[settings.algorithm],
    )
    db.add(RevokedToken(
        jti=payload["jti"],
        expires_at=datetime.fromtimestamp(payload["exp"], tz=timezone.utc),
    ))
    db.commit()
    return {"message": "Sesión cerrada exitosamente"}


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED, **REGISTER_DOCS)
def register(request: RegisterRequest, db: Session = Depends(get_db)):
    data = request.model_dump()

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
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email or ID number already registered",
        )

    return user


@router.post("/login", response_model=TokenResponse, **LOGIN_DOCS)
def login(request: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == request.email).first()

    if not user or not verify_password(request.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if user.user_type_code == "recycler" and user.verification_status != VerificationStatus.verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tu cuenta está pendiente de verificación o fue rechazada",
        )

    token = create_access_token({
        "sub": str(user.id),
        "user_type": user.user_type_code,
        "role": user.role_code,
    })
    return TokenResponse(access_token=token)
