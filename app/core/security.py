import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
import sentry_sdk
from fastapi import Depends, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.errors import ApiError
from app.core.permissions import ensure_role

bearer_scheme = HTTPBearer()
optional_bearer_scheme = HTTPBearer(auto_error=False)

REQUIRED_CLAIMS = ["exp", "iat", "sub", "jti"]


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


def create_access_token(data: dict) -> str:
    now = datetime.now(timezone.utc)
    payload = data.copy()
    payload["jti"] = str(uuid.uuid4())
    payload["iat"] = now
    payload["exp"] = now + timedelta(minutes=settings.access_token_expire_minutes)
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def _unauthorized(code: str, detail: str) -> ApiError:
    return ApiError(
        code,
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def decode_access_token(token: str) -> dict:
    """Verify signature, expiry and required claims. Raises 401 for anything else."""
    try:
        return jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.algorithm],
            options={"require": REQUIRED_CLAIMS},
        )
    except jwt.InvalidTokenError:
        raise _unauthorized("invalid_token", "Token inválido o expirado")


def _user_from_token(token: str, db: Session):
    # Imported here: the models import core.database, which would make a cycle at load time.
    from app.domains.auth.models import RevokedToken
    from app.domains.users.models import User

    payload = decode_access_token(token)
    try:
        user_id = uuid.UUID(payload["sub"])
    except (ValueError, TypeError):
        raise _unauthorized("invalid_token", "Token inválido o expirado")
    jti = payload["jti"]

    # One round trip: the user and, if present, the matching revocation row.
    row = db.execute(
        select(User, RevokedToken.jti)
        .outerjoin(RevokedToken, RevokedToken.jti == jti)
        .where(User.id == user_id)
    ).first()
    if row is None:
        raise _unauthorized("invalid_token", "Token inválido o expirado")

    user, revoked_jti = row
    if revoked_jti is not None:
        raise _unauthorized("session_closed", "La sesión ha sido cerrada")
    if not user.is_active:
        raise _unauthorized("account_disabled", "La cuenta está desactivada")
    # Bumped on password change/reset: every access token issued before that stops working.
    if payload.get("tv", 0) != user.token_version:
        raise _unauthorized("session_outdated", "La sesión ya no es válida. Inicia sesión de nuevo")
    return user


def _identify(request: Request, user) -> None:
    """Make the authenticated user's id visible to the access log and to Sentry (id only, no PII)."""
    request.state.user_id = str(user.id)
    sentry_sdk.set_user({"id": str(user.id)})


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
):
    user = _user_from_token(credentials.credentials, db)
    _identify(request, user)
    return user


def get_optional_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(optional_bearer_scheme),
    db: Session = Depends(get_db),
):
    """The authenticated user, or None for anonymous callers. A bad token is still a 401."""
    if credentials is None:
        return None
    user = _user_from_token(credentials.credentials, db)
    _identify(request, user)
    return user


def require_roles(*roles: str):
    """Dependency factory: 403 unless the caller holds one of `roles`."""
    allowed = frozenset(roles)

    def dependency(user=Depends(get_current_user)):
        ensure_role(user, allowed)
        return user

    return dependency
