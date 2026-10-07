import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from db.tenancy import rls_bypass_reason
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.core.config import get_settings
from api.db.session import engine
from api.routers import analysis, audit, auth, branch_index, github, health, repos

settings = get_settings()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    problem = await rls_bypass_reason(engine)
    if problem and settings.is_production:
        raise RuntimeError(f"refusing to start: {problem}")
    if problem:
        logger.warning("TENANT ISOLATION NOT ENFORCED BY THE DATABASE: %s", problem)
    yield


app = FastAPI(title="revu API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.api_cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(analysis.router)
app.include_router(branch_index.router)
app.include_router(repos.router)
app.include_router(github.router)
app.include_router(audit.router)
