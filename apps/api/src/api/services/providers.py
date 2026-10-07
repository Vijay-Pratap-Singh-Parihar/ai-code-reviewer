"""AI providers and model routes, managed from the UI.

Rules this module enforces (the database enforces tenant isolation on top):

- Credentials go in encrypted (`db.credentials`) and never come back out:
  responses carry only a key hint and header names. Audit entries record
  *that* a key changed, never its value.
- No environment-variable fallback: a review uses exactly the provider and
  model the organisation configured for that step, or it doesn't run.
- Endpoint URLs are checked for SSRF when saved and again before every call
  (`db.ai_endpoints`).
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from db.ai_endpoints import (
    KINDS,
    UnsafeEndpointError,
    base_url_for,
    check_endpoint_url_async,
    credentials_of,
    endpoint_for,
)
from db.audit import AuditAction, record_audit
from db.credentials import CredentialError, encrypt_credentials, key_hint
from db.organization import User
from db.provider import AIProvider, ModelRoute, ModelTier, ProviderKind
from ghapp import is_placeholder_secret
from revu.providers.llm import ModelEndpoint, complete, extract_json
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import get_settings
from api.schemas.providers import (
    ModelRoutePublic,
    ProbeResult,
    ProviderCreate,
    ProviderPublic,
    ProviderSettings,
    ProviderTestResult,
    ProviderUpdate,
    RouteSet,
)

TEST_TIMEOUT_S = 120.0  # a local model's first call also loads it into memory


class ProviderError(ValueError):
    """A request that can't be applied; the message is safe to show."""


class ProviderNameTakenError(ProviderError):
    pass


class NoModelRouteError(Exception):
    """The organisation hasn't chosen a provider + model for this step."""


NO_REVIEW_MODEL = (
    "no review model is configured: add an AI provider and choose a review model on the "
    "AI Providers screen first"
)


# --- configuration ------------------------------------------------------------


def allow_private_endpoints() -> bool:
    configured = get_settings().revu_allow_private_provider_urls
    return (not get_settings().is_production) if configured is None else configured


def encryption_secret() -> str:
    settings = get_settings()
    if settings.is_production and is_placeholder_secret(settings.credential_encryption_key):
        raise ProviderError(
            "CREDENTIAL_ENCRYPTION_KEY is unset or the placeholder; refusing to store "
            "provider credentials with it in production"
        )
    return settings.credential_encryption_key


# --- reading ------------------------------------------------------------------


async def _routes_by_provider(session: AsyncSession) -> dict[uuid.UUID, list[ModelTier]]:
    used: dict[uuid.UUID, list[ModelTier]] = {}
    for route in await session.scalars(select(ModelRoute).order_by(ModelRoute.tier)):
        used.setdefault(route.provider_id, []).append(route.tier)
    return used


def to_public(provider: AIProvider, used_by: list[ModelTier]) -> ProviderPublic:
    header_names: list[str] = []
    has_key = provider.key_hint is not None
    return ProviderPublic(
        id=provider.id,
        name=provider.name,
        kind=provider.kind,
        kind_label=KINDS[provider.kind].label,
        base_url=provider.base_url,
        effective_base_url=base_url_for(provider),
        key_hint=provider.key_hint,
        has_api_key=has_key,
        header_names=sorted((provider.settings or {}).get("_header_names", header_names)),
        settings=ProviderSettings.model_validate(
            {k: v for k, v in (provider.settings or {}).items() if not k.startswith("_")}
        ),
        verified_at=provider.verified_at,
        last_test_error=provider.last_test_error,
        created_at=provider.created_at,
        updated_at=provider.updated_at,
        used_by=used_by,
    )


async def list_providers(session: AsyncSession, org_id: uuid.UUID) -> list[ProviderPublic]:
    used = await _routes_by_provider(session)
    rows = await session.scalars(
        select(AIProvider).where(AIProvider.org_id == org_id).order_by(AIProvider.name)
    )
    return [to_public(p, used.get(p.id, [])) for p in rows]


async def get_provider(
    session: AsyncSession, org_id: uuid.UUID, provider_id: uuid.UUID
) -> AIProvider | None:
    provider: AIProvider | None = await session.scalar(
        select(AIProvider).where(AIProvider.id == provider_id, AIProvider.org_id == org_id)
    )
    return provider


# --- writing ------------------------------------------------------------------


async def _validate_endpoint(kind: ProviderKind, base_url: str | None) -> None:
    info = KINDS[kind]
    if not info.available:
        raise ProviderError(f"{info.label} isn't supported yet")
    if info.needs_base_url and not base_url:
        raise ProviderError(f"{info.label} needs an endpoint URL")
    if base_url:
        try:
            await check_endpoint_url_async(base_url, allow_private=allow_private_endpoints())
        except UnsafeEndpointError as exc:
            raise ProviderError(str(exc)) from exc


def _seal(provider: AIProvider, api_key: str | None, headers: dict[str, str] | None) -> None:
    credentials: dict[str, Any] = {}
    if api_key:
        credentials["api_key"] = api_key
    if headers:
        credentials["headers"] = headers
    provider.encrypted_credentials = encrypt_credentials(
        encryption_secret(), credentials, org_id=provider.org_id, provider_id=provider.id
    )
    provider.key_hint = key_hint(api_key)
    provider.settings = {
        **{k: v for k, v in (provider.settings or {}).items() if k != "_header_names"},
        **({"_header_names": sorted(headers)} if headers else {}),
    }


async def _ensure_name_free(session: AsyncSession, org_id: uuid.UUID, name: str) -> None:
    # Checked up front for a clear error; the unique constraint still guards
    # against two admins saving the same name at the same moment.
    taken = await session.scalar(
        select(AIProvider.id).where(AIProvider.org_id == org_id, AIProvider.name == name)
    )
    if taken is not None:
        raise ProviderNameTakenError(f"a provider named {name!r} already exists")


async def create_provider(
    session: AsyncSession, *, actor: User, body: ProviderCreate
) -> ProviderPublic:
    await _validate_endpoint(body.kind, body.base_url)
    if KINDS[body.kind].needs_api_key and not body.api_key:
        raise ProviderError(f"{KINDS[body.kind].label} needs an API key")

    await _ensure_name_free(session, actor.org_id, body.name)
    provider = AIProvider(
        id=uuid.uuid4(),
        org_id=actor.org_id,
        name=body.name,
        kind=body.kind,
        base_url=body.base_url,
        settings=body.settings.model_dump(exclude_none=True),
        created_by_user_id=actor.id,
    )
    _seal(provider, body.api_key, body.headers)
    session.add(provider)
    record_audit(
        session,
        org_id=actor.org_id,
        actor_id=actor.id,
        action=AuditAction.PROVIDER_CREATED,
        target=f"ai_provider:{provider.name}",
        metadata={"kind": str(body.kind), "base_url": base_url_for(provider)},
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ProviderNameTakenError(f"a provider named {body.name!r} already exists") from exc
    await session.refresh(provider)
    return to_public(provider, [])


async def update_provider(
    session: AsyncSession, *, actor: User, provider: AIProvider, body: ProviderUpdate
) -> ProviderPublic:
    changed: list[str] = []
    fields = body.model_fields_set
    if "base_url" in fields and body.base_url != provider.base_url:
        await _validate_endpoint(provider.kind, body.base_url or None)
        provider.base_url = body.base_url or None
        changed.append("base_url")
    if body.name is not None and body.name != provider.name:
        await _ensure_name_free(session, provider.org_id, body.name)
        provider.name = body.name
        changed.append("name")
    if body.settings is not None:
        hidden = {k: v for k, v in (provider.settings or {}).items() if k.startswith("_")}
        provider.settings = {**body.settings.model_dump(exclude_none=True), **hidden}
        changed.append("settings")
    if "api_key" in fields or "headers" in fields:
        try:
            current = credentials_of(provider, encryption_secret())
        except CredentialError:
            current = {}  # undecryptable: replacing the key is exactly the fix
        api_key = body.api_key if "api_key" in fields else current.get("api_key")
        headers = body.headers if "headers" in fields else current.get("headers")
        if KINDS[provider.kind].needs_api_key and not api_key:
            raise ProviderError(f"{KINDS[provider.kind].label} needs an API key")
        _seal(provider, api_key, headers)
        changed.extend(f for f in ("api_key", "headers") if f in fields)
        # New credentials haven't been tested yet.
        provider.verified_at = None
        provider.last_test_error = None

    if changed:
        provider.updated_at = datetime.now(UTC)
        record_audit(
            session,
            org_id=provider.org_id,
            actor_id=actor.id,
            action=AuditAction.PROVIDER_UPDATED,
            target=f"ai_provider:{provider.name}",
            metadata={"changed": changed},
        )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ProviderNameTakenError(f"a provider named {body.name!r} already exists") from exc
    await session.refresh(provider)
    used = await _routes_by_provider(session)
    return to_public(provider, used.get(provider.id, []))


async def delete_provider(session: AsyncSession, *, actor: User, provider: AIProvider) -> None:
    used = (await _routes_by_provider(session)).get(provider.id, [])
    record_audit(
        session,
        org_id=provider.org_id,
        actor_id=actor.id,
        action=AuditAction.PROVIDER_DELETED,
        target=f"ai_provider:{provider.name}",
        metadata={"kind": str(provider.kind), "routes_removed": [str(t) for t in used]},
    )
    await session.delete(provider)
    await session.commit()


# --- test connection ----------------------------------------------------------

_TOOL = {
    "type": "function",
    "function": {
        "name": "get_line_count",
        "description": "Return how many lines a file in the repository has.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
}


async def _probe(name: str, run: Any) -> tuple[ProbeResult, float]:
    start = time.monotonic()
    try:
        ok, detail, cost = await run()
    except Exception as exc:  # any provider error is a failed probe, not a 500
        return ProbeResult(ok=False, detail=f"{type(exc).__name__}: {str(exc)[:300]}"), 0.0
    return (
        ProbeResult(ok=ok, detail=detail, latency_ms=int((time.monotonic() - start) * 1000)),
        cost,
    )


async def test_provider(
    session: AsyncSession, *, actor: User, provider: AIProvider, model: str
) -> ProviderTestResult:
    """Three small calls: a plain reply, JSON mode, and a tool call. The
    capability flags they find decide how reviews use this provider (a model
    that can't call tools gets diff-only reviews instead of cross-file)."""
    try:
        endpoint = await endpoint_for(
            provider, model, secret=encryption_secret(), allow_private=allow_private_endpoints()
        )
    except (UnsafeEndpointError, CredentialError, ValueError) as exc:
        failed = ProbeResult(ok=False, detail=str(exc))
        result = ProviderTestResult(
            ok=False, model=model, reply=failed, json_mode=failed, tool_calling=failed
        )
        _record_test(session, actor, provider, result)
        await session.commit()
        return result

    async def reply() -> tuple[bool, str | None, float]:
        r = await _call(endpoint, [{"role": "user", "content": "Reply with the single word: OK"}])
        return bool(r.content.strip()), r.content.strip()[:80] or None, r.cost_usd

    async def json_mode() -> tuple[bool, str | None, float]:
        r = await _call(
            endpoint,
            [{"role": "user", "content": 'Return the JSON object {"ok": true} and nothing else.'}],
            response_format={"type": "json_object"},
        )
        return extract_json(r.content).get("ok") is True, None, r.cost_usd

    async def tools() -> tuple[bool, str | None, float]:
        r = await _call(
            endpoint,
            [{"role": "user", "content": "How many lines does src/app.py have? Use the tool."}],
            tools=[_TOOL],
        )
        called = any(c.name == "get_line_count" for c in r.tool_calls)
        return called, None if called else "the model answered without calling the tool", r.cost_usd

    reply_r, c1 = await _probe("reply", reply)
    json_r, c2 = await _probe("json", json_mode) if reply_r.ok else (_skipped(), 0.0)
    tools_r, c3 = await _probe("tools", tools) if reply_r.ok else (_skipped(), 0.0)
    result = ProviderTestResult(
        ok=reply_r.ok and json_r.ok,
        model=model,
        reply=reply_r,
        json_mode=json_r,
        tool_calling=tools_r,
        cost_usd=c1 + c2 + c3,
    )
    if reply_r.ok:
        provider.settings = {
            **(provider.settings or {}),
            "supports_json": json_r.ok,
            "supports_tools": tools_r.ok,
        }
    _record_test(session, actor, provider, result)
    await session.commit()
    return result


def _skipped() -> ProbeResult:
    return ProbeResult(ok=False, detail="skipped: the model didn't reply")


async def _call(endpoint: ModelEndpoint, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
    return await complete(
        model=endpoint, messages=messages, max_tokens=256, timeout=TEST_TIMEOUT_S, **kwargs
    )


def _record_test(
    session: AsyncSession, actor: User, provider: AIProvider, result: ProviderTestResult
) -> None:
    now = datetime.now(UTC)
    if result.ok:
        provider.verified_at = now
        provider.last_test_error = None
    else:
        provider.last_test_error = (
            result.reply.detail or result.json_mode.detail or "the connection test failed"
        )
    record_audit(
        session,
        org_id=provider.org_id,
        actor_id=actor.id,
        action=AuditAction.PROVIDER_TESTED,
        target=f"ai_provider:{provider.name}",
        metadata={
            "model": result.model,
            "ok": result.ok,
            "json": result.json_mode.ok,
            "tools": result.tool_calling.ok,
        },
    )


# --- model list ---------------------------------------------------------------


async def list_models(provider: AIProvider) -> list[str]:
    """The model ids the provider says it serves, for the model picker."""
    base_url = base_url_for(provider)
    if not base_url:
        raise ProviderError("this provider has no endpoint to list models from")
    await check_endpoint_url_async(base_url, allow_private=allow_private_endpoints())
    credentials = credentials_of(provider, encryption_secret())
    headers = dict(credentials.get("headers") or {})
    api_key = credentials.get("api_key")
    if provider.kind == ProviderKind.ANTHROPIC:
        headers |= {"x-api-key": api_key or "", "anthropic-version": "2023-06-01"}
    elif api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
        response = await client.get(f"{base_url.rstrip('/')}/models", headers=headers)
    if response.status_code >= 400:
        raise ProviderError(f"the provider answered {response.status_code} for its model list")
    data = response.json().get("data") or []
    return sorted({str(m["id"]) for m in data if isinstance(m, dict) and m.get("id")})


# --- model routes -------------------------------------------------------------


async def list_routes(session: AsyncSession, org_id: uuid.UUID) -> list[ModelRoutePublic]:
    rows = await session.execute(
        select(ModelRoute, AIProvider.name)
        .join(AIProvider, AIProvider.id == ModelRoute.provider_id)
        .where(ModelRoute.org_id == org_id)
        .order_by(ModelRoute.tier)
    )
    return [
        ModelRoutePublic(
            tier=route.tier,
            provider_id=route.provider_id,
            provider_name=name,
            model=route.model_name,
            updated_at=route.updated_at,
        )
        for route, name in rows.tuples()
    ]


async def set_routes(session: AsyncSession, *, actor: User, body: RouteSet) -> None:
    now = datetime.now(UTC)
    summary: dict[str, str | None] = {}
    for tier_name, update in body.routes.items():
        tier = ModelTier(tier_name)
        route = await session.scalar(
            select(ModelRoute).where(ModelRoute.org_id == actor.org_id, ModelRoute.tier == tier)
        )
        if update is None:
            if route is not None:
                await session.delete(route)
            summary[tier_name] = None
            continue
        provider = await get_provider(session, actor.org_id, update.provider_id)
        if provider is None:
            raise ProviderError("unknown provider")
        if not KINDS[provider.kind].available:
            raise ProviderError(f"{KINDS[provider.kind].label} isn't supported yet")
        if route is None:
            route = ModelRoute(org_id=actor.org_id, tier=tier)
            session.add(route)
        route.provider_id = provider.id
        route.model_name = update.model
        route.updated_at = now
        summary[tier_name] = f"{provider.name}/{update.model}"
    record_audit(
        session,
        org_id=actor.org_id,
        actor_id=actor.id,
        action=AuditAction.MODEL_ROUTES_CHANGED,
        target="model_routes",
        metadata=summary,
    )
    await session.commit()


async def route_snapshot(
    session: AsyncSession, org_id: uuid.UUID, tier: ModelTier
) -> dict[str, Any]:
    """What a queued run records about the model it will use: which
    provider and model (never credentials). The worker re-resolves the
    provider by id, under row-level security, when it runs."""
    row = (
        await session.execute(
            select(ModelRoute, AIProvider)
            .join(AIProvider, AIProvider.id == ModelRoute.provider_id)
            .where(ModelRoute.org_id == org_id, ModelRoute.tier == tier)
        )
    ).first()
    if row is None:
        raise NoModelRouteError(tier)
    route, provider = row
    return {
        "provider_id": str(provider.id),
        "provider_name": provider.name,
        "provider_kind": str(provider.kind),
        "model": route.model_name,
        "supports_tools": (provider.settings or {}).get("supports_tools") is not False,
    }
