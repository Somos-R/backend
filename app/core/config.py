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

    # --- Backoffice (Somos R's own accounts): shorter sessions, stricter limits ---
    backoffice_access_token_minutes: int = 10
    backoffice_refresh_token_hours: int = 12
    mfa_challenge_minutes: int = 5  # time to type the second factor after the password
    # Fernet key that encrypts the TOTP secrets. Required outside dev. Generate one with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    mfa_encryption_key: str | None = None
    # Comma-separated CIDRs allowed to reach /admin (e.g. "203.0.113.0/24,198.51.100.7/32"). Empty = any.
    admin_allowed_cidrs: str = ""

    # --- Email ---
    # "console": log the message (development) · "memory": keep it in an outbox (tests)
    # "resend": send through Resend (needs email_api_key and a verified sender domain)
    email_backend: str = "console"
    email_api_key: str | None = None
    email_from: str = "Somos R <onboarding@resend.dev>"

    # Base URL of the web/mobile app; links in emails point here.
    frontend_url: str = "http://localhost:5173"

    # --- Observability ---
    log_level: str = "INFO"
    # "auto": JSON lines outside dev (what log platforms parse), readable text in dev.
    log_format: Literal["auto", "json", "text"] = "auto"
    # Error tracking. Empty = disabled. Sample rate 0 = errors only, no performance traces.
    sentry_dsn: str | None = None
    sentry_traces_sample_rate: float = 0.0
    sentry_release: str | None = None  # e.g. the git commit SHA
    # Bearer token Prometheus must send to /metrics. Empty = the endpoint does not exist.
    metrics_token: str | None = None

    # --- HTTP surface ---
    # Comma-separated browser origins allowed by CORS. Set this per environment.
    cors_origins: str = "http://localhost:5173,http://localhost:3000,https://somosr.com"
    cors_allow_credentials: bool = True
    # Comma-separated Host headers the API answers to ("*" = any). Set it in staging/prod.
    allowed_hosts: str = "*"
    # Swagger UI / ReDoc / openapi.json. Default: on in dev, off in staging and prod.
    enable_docs: bool | None = None
    # Strict-Transport-Security max-age in seconds; only sent when APP_ENV is staging or prod.
    hsts_max_age: int = 31536000

    # --- Database connection pool (per worker process) ---
    # Total connections = workers x (pool_size + max_overflow). Keep that below the database
    # limit (or the pooler limit, e.g. Supabase). pool_pre_ping is always on.
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout: int = 30  # seconds to wait for a free connection before erroring
    db_pool_recycle: int = 1800  # seconds; replace connections before proxies drop them

    # --- Abuse protection ---
    rate_limit_enabled: bool = True
    rate_limit_storage_uri: str = "memory://"  # e.g. redis://host:6379 to share across workers
    rate_limit_login: str = "10/minute"
    rate_limit_register: str = "10/minute"
    rate_limit_forgot_password: str = "5/minute"
    rate_limit_token_flows: str = "10/minute"  # activate, verify/reset, change password
    rate_limit_admin_auth: str = "5/minute"  # backoffice login and second factor
    rate_limit_admin: str = "60/minute"  # the rest of /admin
    rate_limit_applications: str = "5/minute"  # public: start an application, ask for the access link again
    # Account lockout: after this many consecutive failures the account is locked for
    # 1 minute, doubling with every further failure up to the cap.
    login_max_attempts: int = 5
    login_lock_max_minutes: int = 60

    # --- One-time token lifetimes (minutes) ---
    activation_token_minutes: int = 60 * 48
    email_verification_token_minutes: int = 60 * 24
    password_reset_token_minutes: int = 60

    # --- Applications to join (public onboarding) ---
    application_link_days: int = 30  # how long the applicant's magic link works (renewed on every new link)
    application_max_submissions: int = 3  # sends allowed: the first one plus corrections after a review
    application_consent_version: str = "2026-10"  # version of the data-treatment text the web shows

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
        self.admin_networks  # fails at startup, not on the first request, if a CIDR is malformed
        if self.mfa_encryption_key:
            try:
                from cryptography.fernet import Fernet
                Fernet(self.mfa_encryption_key.encode())
            except Exception:
                raise ValueError("MFA_ENCRYPTION_KEY is not a valid Fernet key")
        elif self.app_env != "dev":
            raise ValueError(
                "MFA_ENCRYPTION_KEY is required when APP_ENV=staging|prod (it encrypts the TOTP secrets). "
                "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
        if weak:
            logger.warning(
                "SECRET_KEY is weak (short or a placeholder). Fine for local development; "
                "startup will fail with APP_ENV=staging|prod."
            )
        return self

    @property
    def use_json_logs(self) -> bool:
        return self.log_format == "json" or (self.log_format == "auto" and self.app_env != "dev")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.cors_origins.split(",") if o.strip()]

    @property
    def allowed_host_list(self) -> list[str]:
        return [h.strip() for h in self.allowed_hosts.split(",") if h.strip()] or ["*"]

    @property
    def admin_networks(self) -> list:
        import ipaddress
        return [ipaddress.ip_network(c.strip(), strict=False) for c in self.admin_allowed_cidrs.split(",") if c.strip()]

    @property
    def docs_enabled(self) -> bool:
        return self.enable_docs if self.enable_docs is not None else self.app_env == "dev"

    def deployment_warnings(self) -> list[str]:
        """Settings that are legal but risky outside development. Logged at startup."""
        if self.app_env == "dev":
            return []
        found = []
        if self.email_backend == "console":
            found.append("EMAIL_BACKEND=console: activation and reset links are only written to the log")
        if self.rate_limit_storage_uri.startswith("memory"):
            found.append("RATE_LIMIT_STORAGE_URI is in-memory: limits are per worker process, not shared")
        if "*" in self.allowed_host_list:
            found.append("ALLOWED_HOSTS=* accepts any Host header")
        if any("localhost" in o or "127.0.0.1" in o for o in self.cors_origin_list):
            found.append("CORS_ORIGINS still lists localhost origins")
        if not self.sentry_dsn:
            found.append("SENTRY_DSN is empty: unhandled errors are only in the logs, nobody is alerted")
        if self.metrics_token and len(self.metrics_token) < 16:
            found.append("METRICS_TOKEN is shorter than 16 characters")
        if not self.admin_networks:
            found.append("ADMIN_ALLOWED_CIDRS is empty: /admin is reachable from any address")
        if self.frontend_url.startswith("http://localhost"):
            found.append("FRONTEND_URL points to localhost: links in emails will not work")
        return found


settings = Settings()
