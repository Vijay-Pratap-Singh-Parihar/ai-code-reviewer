import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from db.tenancy import rls_bypass_reason
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from observability import (
    REQUEST_ID_HEADER,
    configure_logging,
    default_format,
    init_error_reporting,
)

from api.core.config import get_settings
from api.core.request_context import RequestContextMiddleware
from api.core.security_headers import SecurityHeadersMiddleware
from api.db.session import engine
from api.routers import (
    analysis,
    audit,
    auth,
    branch_index,
    github,
    health,
    metrics,
    providers,
    repos,
)

settings = get_settings()
configure_logging(
    service="api",
    level=settings.log_level,
    fmt=settings.log_format or default_format(settings.environment),
)
# Sentry's FastAPI integration switches itself on when the SDK starts.
init_error_reporting(
    dsn=settings.sentry_dsn,
    service="api",
    environment=settings.environment,
    traces_sample_rate=settings.sentry_traces_sample_rate,
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    problem = await rls_bypass_reason(engine)
    if problem and settings.is_production:
        raise RuntimeError(f"refusing to start: {problem}")
    if problem:
        logger.warning("TENANT ISOLATION NOT ENFORCED BY THE DATABASE: %s", problem)
    yield


# The interactive API docs are for development; production doesn't publish
# its API surface to whoever asks.
_docs = not settings.is_production
app = FastAPI(
    title="revu API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if _docs else None,
    redoc_url="/redoc" if _docs else None,
    openapi_url="/openapi.json" if _docs else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.api_cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[REQUEST_ID_HEADER],
)
app.add_middleware(SecurityHeadersMiddleware, hsts=settings.is_production)
# Outermost, so the request id covers everything below it, CORS included.
app.add_middleware(RequestContextMiddleware)

app.include_router(health.router)
app.include_router(metrics.router)
app.include_router(auth.router)
app.include_router(analysis.router)
app.include_router(branch_index.router)
app.include_router(repos.router)
app.include_router(github.router)
app.include_router(audit.router)
app.include_router(providers.router)
