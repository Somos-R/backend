import logging
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

MIN_SECRET_KEY_LENGTH = 32
PLACEHOLDER_SECRETS = frozenset({
    "dev-secret-key-change-in-production", "change-me", "changeme", "secret", "secret-key",
    "your-secret-key", "supersecret", "test", "password",
})
ALLOWED_ALGORITHMS = frozenset({"HS256", "HS384", "HS512"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env.local",
        env_file_encoding="utf-8",
    )

    # "dev" only warns about weak secrets; "staging"/"prod" refuse to start with them.
    app_env: Literal["dev", "staging", "prod"] = "dev"

    database_url: str
    secret_key: str
    algorithm: str = "HS256"
    # Short-lived by design; clients renew with the refresh token (POST /auth/refresh).
    access_token_expire_minutes: int = 30
    refresh_token_days: int = 30

    # --- Email ---
    # "console": log the message (development) · "memory": keep it in an outbox (tests)
    # "resend": send through Resend (needs email_api_key and a verified sender domain)
    email_backend: str = "console"
    email_api_key: str | None = None
    email_from: str = "Somos R <onboarding@resend.dev>"

    # Base URL of the web/mobile app; links in emails point here.
    frontend_url: str = "http://localhost:5173"

    # --- Abuse protection ---
    rate_limit_enabled: bool = True
    rate_limit_storage_uri: str = "memory://"  # e.g. redis://host:6379 to share across workers
    rate_limit_login: str = "10/minute"
    rate_limit_register: str = "10/minute"
    rate_limit_forgot_password: str = "5/minute"
    rate_limit_token_flows: str = "10/minute"  # activate, verify/reset, change password
    # Account lockout: after this many consecutive failures the account is locked for
    # 1 minute, doubling with every further failure up to the cap.
    login_max_attempts: int = 5
    login_lock_max_minutes: int = 60

    # --- One-time token lifetimes (minutes) ---
    activation_token_minutes: int = 60 * 48
    email_verification_token_minutes: int = 60 * 24
    password_reset_token_minutes: int = 60

    @model_validator(mode="after")
    def check_secrets(self):
        if self.algorithm not in ALLOWED_ALGORITHMS:
            raise ValueError(f"ALGORITHM must be one of {sorted(ALLOWED_ALGORITHMS)}")

        weak = (
            len(self.secret_key) < MIN_SECRET_KEY_LENGTH
            or self.secret_key.lower() in PLACEHOLDER_SECRETS
        )
        if weak and self.app_env != "dev":
            raise ValueError(
                f"SECRET_KEY must be at least {MIN_SECRET_KEY_LENGTH} characters and not a placeholder "
                f"when APP_ENV={self.app_env}. Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
            )
        if weak:
            logger.warning(
                "SECRET_KEY is weak (short or a placeholder). Fine for local development; "
                "startup will fail with APP_ENV=staging|prod."
            )
        return self


settings = Settings()
