import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.domains.users.docs import GET_USER_DOCS, LIST_USERS_DOCS, UPDATE_USER_DOCS
from app.domains.users.models import User
from app.domains.users.schemas import UpdateUserRequest, UserDetailResponse, UserListResponse

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=UserListResponse, **LIST_USERS_DOCS)
def list_users(
    user_type_code: str | None = Query(default=None),
    role_code: str | None = Query(default=None),
    verification_status: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    query = db.query(User)

    if user_type_code:
        query = query.filter(User.user_type_code == user_type_code)
    if role_code:
        query = query.filter(User.role_code == role_code)
    if verification_status:
        query = query.filter(User.verification_status == verification_status)

    total = query.count()
    users = query.order_by(User.created_at.desc()).offset(offset).limit(limit).all()

    return UserListResponse(total=total, limit=limit, offset=offset, items=users)


@router.get("/{user_id}", response_model=UserDetailResponse, **GET_USER_DOCS)
def get_user(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado")
    return user


@router.patch("/{user_id}", response_model=UserDetailResponse, **UPDATE_USER_DOCS)
def update_user(
    user_id: uuid.UUID,
    request: UpdateUserRequest,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado")

    for field in request.model_fields_set:
        setattr(user, field, getattr(request, field))

    try:
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="tax_id already registered",
        )

    return user
