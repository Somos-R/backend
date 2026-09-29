import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.core.config import settings
from app.core.health import router as health_router
from app.core.rate_limit import limiter, rate_limit_exceeded_handler
from app.core.security_headers import SecurityHeadersMiddleware
from app.domains.auth.router import router as auth_router
from app.domains.catalogs.router import router as catalogs_router
from app.domains.inventory.router import router as inventory_router
from app.domains.transactions.router import router as transactions_router
from app.domains.users.router import router as users_router
from app.domains.weighings.router import router as weighings_router

logger = logging.getLogger(__name__)

CORS_METHODS = ["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"]
CORS_HEADERS = ["Authorization", "Content-Type", "Accept"]


def create_app() -> FastAPI:
    """Build the application from the current settings (a function so tests can vary them)."""
    docs = settings.docs_enabled
    app = FastAPI(
        title="Somos R API",
        description="Backend API for Somos R recycling platform",
        version="0.1.0",
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )

    for warning in settings.deployment_warnings():
        logger.warning("Deployment check (%s): %s", settings.app_env, warning)

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)  # type: ignore[arg-type]

    # Middleware added last runs first: host check, then CORS, then the security headers.
    hsts = settings.hsts_max_age if settings.app_env != "dev" else None
    app.add_middleware(SecurityHeadersMiddleware, hsts_max_age=hsts)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=CORS_METHODS,
        allow_headers=CORS_HEADERS,
    )
    if settings.allowed_host_list != ["*"]:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_host_list)

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(catalogs_router)
    app.include_router(users_router)
    app.include_router(inventory_router)
    app.include_router(weighings_router)
    app.include_router(transactions_router)
    return app


app = create_app()
