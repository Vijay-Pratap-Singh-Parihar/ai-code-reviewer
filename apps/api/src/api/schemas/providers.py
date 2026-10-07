import uuid
from datetime import datetime
from typing import Literal

from db.provider import ModelTier, ProviderKind
from pydantic import BaseModel, Field, field_validator


class ProviderKindInfo(BaseModel):
    kind: ProviderKind
    label: str
    needs_api_key: bool
    needs_base_url: bool
    default_base_url: str | None
    available: bool


class ProviderSettings(BaseModel):
    """Non-secret configuration. Capability flags are normally filled in by
    "Test connection"; prices matter only for models LiteLLM can't price
    (self-hosted or fine-tuned), per million tokens."""

    model_config = {"extra": "forbid"}

    supports_tools: bool | None = None
    supports_json: bool | None = None
    context_window: int | None = Field(default=None, ge=1024, le=10_000_000)
    input_cost_per_mtok: float | None = Field(default=None, ge=0)
    output_cost_per_mtok: float | None = Field(default=None, ge=0)


class ProviderCreate(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = Field(min_length=1, max_length=100)
    kind: ProviderKind
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=4096)
    # Extra request headers some gateways need (e.g. an enterprise proxy's
    # auth header). Stored encrypted with the key.
    headers: dict[str, str] | None = None
    settings: ProviderSettings = Field(default_factory=ProviderSettings)

    @field_validator("name", "base_url", "api_key")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        return value.strip() or None if isinstance(value, str) else value


class ProviderUpdate(BaseModel):
    """Every field optional. `api_key`/`headers` absent = keep the stored
    ones; `api_key: ""` clears the key (for servers that need none)."""

    model_config = {"extra": "forbid"}

    name: str | None = Field(default=None, min_length=1, max_length=100)
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=4096)
    headers: dict[str, str] | None = None
    settings: ProviderSettings | None = None


class ProviderPublic(BaseModel):
    """A provider as the API shows it: never the key, only its hint."""

    id: uuid.UUID
    name: str
    kind: ProviderKind
    kind_label: str
    base_url: str | None
    effective_base_url: str | None
    key_hint: str | None
    has_api_key: bool
    header_names: list[str] = Field(default_factory=list)
    settings: ProviderSettings
    verified_at: datetime | None
    last_test_error: str | None
    created_at: datetime
    updated_at: datetime | None
    used_by: list[ModelTier] = Field(default_factory=list)


class ProviderTestRequest(BaseModel):
    model: str = Field(min_length=1, max_length=255)


class ProbeResult(BaseModel):
    ok: bool
    detail: str | None = None
    latency_ms: int | None = None


class ProviderTestResult(BaseModel):
    ok: bool
    model: str
    reply: ProbeResult
    json_mode: ProbeResult
    tool_calling: ProbeResult
    cost_usd: float = 0.0


class ModelList(BaseModel):
    models: list[str]


class ModelRoutePublic(BaseModel):
    tier: ModelTier
    provider_id: uuid.UUID
    provider_name: str
    model: str
    updated_at: datetime | None


class ModelRouteUpdate(BaseModel):
    model_config = {"extra": "forbid"}

    provider_id: uuid.UUID
    model: str = Field(min_length=1, max_length=255)


class RouteSet(BaseModel):
    """`null` removes the route for that tier."""

    model_config = {"extra": "forbid"}

    routes: dict[Literal["screen", "review", "verify"], ModelRouteUpdate | None]
