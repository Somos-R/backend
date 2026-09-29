from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env.local",
        env_file_encoding="utf-8",
    )

    database_url: str
    secret_key: str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30

    # --- Email ---
    # "console": log the message (development) · "memory": keep it in an outbox (tests)
    # "resend": send through Resend (needs email_api_key and a verified sender domain)
    email_backend: str = "console"
    email_api_key: str | None = None
    email_from: str = "Somos R <onboarding@resend.dev>"

    # Base URL of the web/mobile app; links in emails point here.
    frontend_url: str = "http://localhost:5173"

    # --- One-time token lifetimes (minutes) ---
    activation_token_minutes: int = 60 * 48
    email_verification_token_minutes: int = 60 * 24
    password_reset_token_minutes: int = 60


settings = Settings()
