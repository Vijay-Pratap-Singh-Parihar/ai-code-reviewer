"""From a stored AI provider to a callable model endpoint.

Shared by the API ("Test connection", model lists) and the worker (reviews)
so both resolve providers the same way:

- which LiteLLM model string each provider kind uses;
- decrypting the provider's credentials (`db.credentials`);
- checking that an endpoint URL is safe to call.

URL safety. An organisation admin can point revu at any URL, and the API and
worker will then make requests to it from inside the deployment. Without a
check that is a server-side request forgery: e.g. `http://169.254.169.254/`
would read the cloud instance's metadata and credentials. So:

- only http/https;
- link-local and cloud-metadata addresses are always refused;
- private and loopback addresses (an Ollama on the host, a vLLM server on
  the company network) are allowed only where the deployment opts in
  (`allow_private=True`; on by default outside production).

The check resolves the hostname and checks every address it resolves to,
and runs again right before each call, not only when the URL is saved, so a
DNS record changed after saving can't slip past it.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from revu.providers.llm import ModelEndpoint

from db.credentials import decrypt_credentials
from db.provider import AIProvider, ProviderKind


class UnsafeEndpointError(ValueError):
    """The URL points somewhere revu must not send requests."""


class ProviderNotSupportedError(ValueError):
    """The provider kind is reserved for a later integration."""


@dataclass(frozen=True)
class KindInfo:
    label: str
    litellm_prefix: str
    needs_api_key: bool
    needs_base_url: bool
    default_base_url: str | None
    available: bool = True


KINDS: dict[ProviderKind, KindInfo] = {
    ProviderKind.ANTHROPIC: KindInfo(
        "Anthropic", "anthropic/", True, False, "https://api.anthropic.com/v1"
    ),
    ProviderKind.OPENAI: KindInfo("OpenAI", "openai/", True, False, "https://api.openai.com/v1"),
    ProviderKind.GROQ: KindInfo("Groq", "groq/", True, False, "https://api.groq.com/openai/v1"),
    ProviderKind.OPENAI_COMPATIBLE: KindInfo(
        "OpenAI-compatible endpoint", "openai/", False, True, None
    ),
    ProviderKind.BEDROCK: KindInfo("AWS Bedrock", "bedrock/", True, False, None, available=False),
    ProviderKind.AZURE: KindInfo("Azure AI Foundry", "azure/", True, True, None, available=False),
    ProviderKind.VERTEX: KindInfo(
        "Google Vertex AI", "vertex_ai/", True, False, None, available=False
    ),
}

# Servers that need no key (Ollama, most local vLLM) still get a placeholder,
# so LiteLLM never falls back to an API key found in the environment.
NO_KEY_PLACEHOLDER = "not-needed"


def base_url_for(provider: AIProvider) -> str | None:
    return provider.base_url or KINDS[provider.kind].default_base_url


def _check_address(address: str, *, allow_private: bool) -> None:
    ip = ipaddress.ip_address(address)
    if ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        raise UnsafeEndpointError(f"{address} is a link-local or reserved address")
    if (ip.is_private or ip.is_loopback) and not allow_private:
        raise UnsafeEndpointError(
            f"{address} is a private or loopback address; this deployment does not allow "
            "AI provider endpoints on private networks"
        )


def check_endpoint_url(url: str, *, allow_private: bool) -> None:
    """Validate scheme and every address `url`'s host resolves to."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise UnsafeEndpointError("the endpoint must be an http:// or https:// URL")
    if parsed.username or parsed.password:
        raise UnsafeEndpointError("put credentials in the API key field, not in the URL")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeEndpointError(f"can't resolve {parsed.hostname}: {exc}") from exc
    for info in infos:
        _check_address(str(info[4][0]), allow_private=allow_private)


async def check_endpoint_url_async(url: str, *, allow_private: bool) -> None:
    await asyncio.to_thread(check_endpoint_url, url, allow_private=allow_private)


def credentials_of(provider: AIProvider, secret: str) -> dict[str, Any]:
    return decrypt_credentials(
        secret, provider.encrypted_credentials, org_id=provider.org_id, provider_id=provider.id
    )


async def endpoint_for(
    provider: AIProvider, model_name: str, *, secret: str, allow_private: bool
) -> ModelEndpoint:
    """The callable endpoint for `model_name` on `provider`, with its key
    decrypted and its URL checked."""
    info = KINDS[provider.kind]
    if not info.available:
        raise ProviderNotSupportedError(f"{info.label} is not supported yet")
    base_url = base_url_for(provider)
    if provider.kind == ProviderKind.OPENAI_COMPATIBLE:
        if not base_url:
            raise UnsafeEndpointError("an OpenAI-compatible provider needs an endpoint URL")
        await check_endpoint_url_async(base_url, allow_private=allow_private)
    credentials = credentials_of(provider, secret)
    settings = provider.settings or {}
    return ModelEndpoint(
        model=f"{info.litellm_prefix}{model_name}",
        api_key=credentials.get("api_key") or NO_KEY_PLACEHOLDER,
        # Hosted providers use LiteLLM's built-in URL unless overridden.
        api_base=base_url if provider.kind == ProviderKind.OPENAI_COMPATIBLE else provider.base_url,
        extra_headers=credentials.get("headers") or None,
        supports_tools=settings.get("supports_tools") is not False,
        input_cost_per_mtok=settings.get("input_cost_per_mtok"),
        output_cost_per_mtok=settings.get("output_cost_per_mtok"),
    )
