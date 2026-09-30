import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domains.users.enums import VerificationStatus
from app.domains.users.schemas import UserDetailResponse


class AdminUserSummary(BaseModel):
    """What a list needs: enough to find and act on someone, without their whole profile."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    user_type_code: str
    role_code: str | None
    organization_id: uuid.UUID | None
    is_active: bool
    verification_status: VerificationStatus | None
    email_verified: bool
    locked: bool  # temporarily blocked after failed sign-ins
    pending_activation: bool  # invited or verified, but has not chosen a password yet
    created_at: datetime


class AdminUserListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[AdminUserSummary]


class AdminUserDetail(UserDetailResponse):
    """The full profile plus the security state of the account."""

    locked: bool
    locked_until: datetime | None
    failed_login_attempts: int
    pending_activation: bool
    mfa_enabled: bool
    organization_name: str | None


class SetActiveRequest(BaseModel):
    is_active: bool
    reason: str | None = Field(default=None, max_length=200)


class ChangeRoleRequest(BaseModel):
    role_code: str = Field(min_length=1, max_length=20)


class AssignOrganizationRequest(BaseModel):
    organization_id: uuid.UUID
