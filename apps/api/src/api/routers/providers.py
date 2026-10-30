"""The AI Providers screen's API. Reading is open to every member (nothing
secret is ever returned); changing, testing and listing a provider's models
need an organisation owner or admin."""

import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated

from db.ai_endpoints import KINDS
from db.credentials import CredentialError
from db.provider import ProviderKind
from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.deps import CurrentUser, OrgAdmin
from api.db.session import get_db
from api.schemas.providers import (
    EndpointPreset,
    ModelList,
    ModelRoutePublic,
    ProviderCreate,
    ProviderKindInfo,
    ProviderPublic,
    ProviderTestRequest,
    ProviderTestResult,
    ProviderUpdate,
    RouteSet,
)
from api.services import providers as service

router = APIRouter(prefix="/providers", tags=["ai-providers"])

Db = Annotated[AsyncSession, Depends(get_db)]


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, service.ProviderNameTakenError):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, CredentialError):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


async def _require(db: AsyncSession, org_id: uuid.UUID, provider_id: uuid.UUID):  # type: ignore[no-untyped-def]
    provider = await service.get_provider(db, org_id, provider_id)
    if provider is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "AI provider not found")
    return provider


def this_machine() -> str:
    """The host name revu's API (and worker, which runs the same way) uses
    for the machine it runs on: from inside a container that's the Docker
    host, otherwise loopback."""
    return "host.docker.internal" if Path("/.dockerenv").exists() else "127.0.0.1"


def _presets(kind: ProviderKind) -> list[EndpointPreset]:
    if kind != ProviderKind.OPENAI_COMPATIBLE:
        return []
    host = this_machine()
    return [
        EndpointPreset(label="Ollama on this machine", url=f"http://{host}:11434/v1"),
        EndpointPreset(label="LM Studio on this machine", url=f"http://{host}:1234/v1"),
        EndpointPreset(label="vLLM on this machine", url=f"http://{host}:8000/v1"),
    ]


@router.get("/kinds", response_model=list[ProviderKindInfo])
async def list_kinds(_: CurrentUser) -> list[ProviderKindInfo]:
    return [
        ProviderKindInfo(
            kind=kind,
            label=info.label,
            needs_api_key=info.needs_api_key,
            needs_base_url=info.needs_base_url,
            default_base_url=info.default_base_url,
            available=info.available,
            presets=_presets(kind),
        )
        for kind, info in KINDS.items()
    ]


@router.get("/routes", response_model=list[ModelRoutePublic])
async def get_routes(user: CurrentUser, db: Db) -> list[ModelRoutePublic]:
    return await service.list_routes(db, user.org_id)


@router.put("/routes", response_model=list[ModelRoutePublic])
async def put_routes(body: RouteSet, user: OrgAdmin, db: Db) -> list[ModelRoutePublic]:
    try:
        await service.set_routes(db, actor=user, body=body)
    except service.ProviderError as exc:
        raise _http(exc) from exc
    return await service.list_routes(db, user.org_id)


@router.get("", response_model=list[ProviderPublic])
async def list_providers(user: CurrentUser, db: Db) -> list[ProviderPublic]:
    return await service.list_providers(db, user.org_id)


@router.post("", response_model=ProviderPublic, status_code=status.HTTP_201_CREATED)
async def create_provider(body: ProviderCreate, user: OrgAdmin, db: Db) -> ProviderPublic:
    try:
        return await service.create_provider(db, actor=user, body=body)
    except service.ProviderError as exc:
        raise _http(exc) from exc


@router.patch("/{provider_id}", response_model=ProviderPublic)
async def update_provider(
    provider_id: uuid.UUID, body: ProviderUpdate, user: OrgAdmin, db: Db
) -> ProviderPublic:
    provider = await _require(db, user.org_id, provider_id)
    try:
        return await service.update_provider(db, actor=user, provider=provider, body=body)
    except service.ProviderError as exc:
        raise _http(exc) from exc


@router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(provider_id: uuid.UUID, user: OrgAdmin, db: Db) -> Response:
    provider = await _require(db, user.org_id, provider_id)
    await service.delete_provider(db, actor=user, provider=provider)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{provider_id}/test", response_model=ProviderTestResult)
async def test_provider(
    provider_id: uuid.UUID, body: ProviderTestRequest, user: OrgAdmin, db: Db
) -> ProviderTestResult:
    """Makes three small, real calls to the provider (a few hundred tokens
    in total; billed by paid providers)."""
    provider = await _require(db, user.org_id, provider_id)
    try:
        return await service.test_provider(
            db, actor=user, provider=provider, model=body.model, reasoning=body.reasoning
        )
    except service.ProviderError as exc:
        raise _http(exc) from exc


@router.post("/{provider_id}/test/stream")
async def test_provider_stream(
    provider_id: uuid.UUID, body: ProviderTestRequest, user: OrgAdmin, db: Db
) -> StreamingResponse:
    """The same test, reported live as newline-delimited JSON: an event when
    each call starts and ends, then the recorded result (see
    `service.run_provider_test`)."""
    provider = await _require(db, user.org_id, provider_id)
    events = service.run_provider_test(
        db, actor=user, provider=provider, model=body.model, reasoning=body.reasoning
    )

    async def lines() -> AsyncIterator[str]:
        async for event in events:
            yield json.dumps(event) + "\n"

    # No proxy buffering: each line should reach the browser as it's written.
    return StreamingResponse(
        lines(), media_type="application/x-ndjson", headers={"X-Accel-Buffering": "no"}
    )


@router.get("/{provider_id}/models", response_model=ModelList)
async def list_models(provider_id: uuid.UUID, user: OrgAdmin, db: Db) -> ModelList:
    provider = await _require(db, user.org_id, provider_id)
    try:
        return ModelList(models=await service.list_models(provider))
    except (service.ProviderError, CredentialError, ValueError) as exc:
        raise _http(exc) from exc
    except Exception as exc:  # network errors talking to the provider
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"couldn't reach the provider: {type(exc).__name__}"
        ) from exc
