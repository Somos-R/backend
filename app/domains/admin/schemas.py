import uuid
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class MfaChallengeResponse(BaseModel):
    """Password accepted; the second factor is still needed to open the session."""

    mfa_token: str
    mfa_status: Literal["code_required", "enrollment_required"]
    expires_in: int  # seconds left to complete the second factor


class MfaTokenRequest(BaseModel):
    mfa_token: str


class MfaEnrollStartResponse(BaseModel):
    secret: str  # base32, for typing into the authenticator app by hand
    otpauth_uri: str  # what the client draws as a QR code


class MfaEnrollConfirmRequest(BaseModel):
    mfa_token: str
    code: str = Field(min_length=6, max_length=8)


class MfaVerifyRequest(BaseModel):
    """Either the 6-digit code from the authenticator or one recovery code, never both."""

    mfa_token: str
    code: str | None = Field(default=None, min_length=6, max_length=8)
    recovery_code: str | None = Field(default=None, min_length=8, max_length=16)

    @model_validator(mode="after")
    def exactly_one(self):
        if (self.code is None) == (self.recovery_code is None):
            raise ValueError("Envía `code` o `recovery_code`, no ambos ni ninguno")
        return self


class BackofficeSessionResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_token: str
    # Shown once, at enrollment. Store them somewhere safe: they replace the authenticator.
    recovery_codes: list[str] | None = None
    # Present when a recovery code was used to sign in.
    recovery_codes_remaining: int | None = None


class RecoveryCodesRequest(BaseModel):
    code: str = Field(min_length=6, max_length=8)


class RecoveryCodesResponse(BaseModel):
    recovery_codes: list[str]


class AdminMeResponse(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    user_type_code: str
    role_code: str | None
    capabilities: list[str]
    mfa_enabled: bool
    recovery_codes_remaining: int
