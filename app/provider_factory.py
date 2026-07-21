from __future__ import annotations

import inspect
import os
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

import httpx
from agent_framework_foundry import FoundryChatClient
from agent_framework_openai import OpenAIChatClient, OpenAIChatCompletionClient
from agent_framework_anthropic import AnthropicFoundryClient
from anthropic import AsyncAnthropicFoundry
from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider
from openai import AsyncAzureOpenAI, AsyncOpenAI

from app.foundry_config import (
    ApiType,
    AuthType,
    EndpointKind,
    FoundrySettings,
    ResolvedFoundrySettings,
    VersionMode,
    claude_base_url,
    openai_v1_base_url,
)

AzureScope = Literal["https://ai.azure.com/.default", "https://cognitiveservices.azure.com/.default"]
CredentialFactory = Callable[[], Any]
SettingsSnapshot = FoundrySettings | ResolvedFoundrySettings
HttpClientFactory = Callable[[ResolvedFoundrySettings, "RouteDescriptor"], httpx.AsyncClient]


@dataclass(frozen=True, slots=True)
class RouteDescriptor:
    provider: str
    base_url: str
    request_url: str
    auth_type: AuthType
    expected_auth_header: Literal["Authorization", "api-key"]


@dataclass(slots=True)
class ProviderBundle(AbstractAsyncContextManager["ProviderBundle"]):
    client: Any
    route: RouteDescriptor
    raw_client: Any | None = None
    _resources: list[Any] = field(default_factory=list)
    _closed: bool = False

    async def __aenter__(self) -> "ProviderBundle":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        await self.close()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        seen: set[int] = set()
        for resource in reversed(self._resources):
            if resource is None or id(resource) in seen:
                continue
            seen.add(id(resource))
            closer = getattr(resource, "close", None) or getattr(resource, "aclose", None)
            if not closer:
                continue
            result = closer()
            if inspect.isawaitable(result):
                await result


class ProviderFactory:
    """Build MAF provider clients from a validated Foundry settings snapshot."""

    def __init__(
        self,
        credential_factory: CredentialFactory | None = None,
        http_client_factory: HttpClientFactory | None = None,
    ):
        self._credential_factory = credential_factory or (
            lambda: DefaultAzureCredential(
                exclude_interactive_browser_credential=os.environ.get("MCPCLIENT_HEADLESS") == "1"
            )
        )
        self._http_client_factory = http_client_factory

    @staticmethod
    def _resolve(settings: SettingsSnapshot) -> ResolvedFoundrySettings:
        return settings.resolve() if isinstance(settings, FoundrySettings) else settings

    def describe_route(self, settings: SettingsSnapshot) -> RouteDescriptor:
        settings = self._resolve(settings)
        auth_header: Literal["Authorization", "api-key"]
        auth_header = "api-key" if settings.auth.type == AuthType.API_KEY else "Authorization"
        if settings.endpoint_kind == EndpointKind.PROJECT:
            base_url = openai_v1_base_url(settings)
            return RouteDescriptor(
                provider="foundry_responses",
                base_url=base_url,
                request_url=f"{base_url}responses",
                auth_type=settings.auth.type,
                expected_auth_header="Authorization",
            )
        if settings.api_type == ApiType.CLAUDE_MESSAGES:
            base_url = claude_base_url(settings)
            return RouteDescriptor(
                provider="anthropic_foundry",
                base_url=base_url,
                request_url=f"{base_url}/v1/messages?beta=true",
                auth_type=settings.auth.type,
                expected_auth_header=auth_header,
            )
        if settings.version_mode == VersionMode.V1:
            base_url = openai_v1_base_url(settings)
            operation = "responses" if settings.api_type == ApiType.RESPONSES else "chat/completions"
            return RouteDescriptor(
                provider="openai_responses" if settings.api_type == ApiType.RESPONSES else "openai_chat_completions",
                base_url=base_url,
                request_url=f"{base_url}{operation}",
                auth_type=settings.auth.type,
                expected_auth_header="Authorization",
            )
        query = f"?api-version={settings.api_version}"
        if settings.api_type == ApiType.RESPONSES:
            request_url = f"{settings.endpoint}/openai/responses{query}"
        else:
            request_url = (
                f"{settings.endpoint}/openai/deployments/{settings.model}/chat/completions{query}"
            )
        return RouteDescriptor(
            provider="azure_openai_responses" if settings.api_type == ApiType.RESPONSES else "azure_openai_chat_completions",
            base_url=settings.endpoint,
            request_url=request_url,
            auth_type=settings.auth.type,
            expected_auth_header=auth_header,
        )

    def create(self, settings: SettingsSnapshot) -> ProviderBundle:
        settings = self._resolve(settings)
        route = self.describe_route(settings)
        if settings.endpoint_kind == EndpointKind.PROJECT:
            credential = self._credential_factory()
            client = FoundryChatClient(
                project_endpoint=settings.endpoint,
                model=settings.model,
                credential=credential,
            )
            resources = [getattr(client, "client", None), getattr(client, "project_client", None), credential]
            return ProviderBundle(
                client=client,
                route=route,
                raw_client=getattr(client, "client", None),
                _resources=resources,
            )

        http_client = self._http_client_factory(settings, route) if self._http_client_factory else None

        if settings.api_type == ApiType.CLAUDE_MESSAGES:
            credential = None
            token_provider = None
            if settings.auth.type == AuthType.ENTRA_ID:
                credential = self._credential_factory()
                token_provider = get_bearer_token_provider(credential, "https://ai.azure.com/.default")
            raw_client = AsyncAnthropicFoundry(
                base_url=route.base_url,
                api_key=settings.api_key,
                azure_ad_token_provider=token_provider,
                http_client=http_client,
            )
            client = AnthropicFoundryClient(
                model=settings.model,
                anthropic_client=raw_client,
            )
            return ProviderBundle(
                client=client,
                route=route,
                raw_client=raw_client,
                _resources=[raw_client, credential],
            )

        if settings.version_mode == VersionMode.V1:
            credential = None
            api_key: str | Callable[[], Awaitable[str]] | None = settings.api_key
            if settings.auth.type == AuthType.ENTRA_ID:
                credential = self._credential_factory()
                api_key = get_bearer_token_provider(credential, "https://ai.azure.com/.default")
            raw_client = AsyncOpenAI(base_url=route.base_url, api_key=api_key, http_client=http_client)
        else:
            credential = None
            azure_kwargs: dict[str, Any] = {
                "azure_endpoint": settings.endpoint,
                "api_version": settings.api_version,
                "http_client": http_client,
            }
            if settings.api_type == ApiType.CHAT_COMPLETIONS:
                azure_kwargs["azure_deployment"] = settings.model
            if settings.auth.type == AuthType.API_KEY:
                azure_kwargs["api_key"] = settings.api_key
            else:
                credential = self._credential_factory()
                azure_kwargs["azure_ad_token_provider"] = get_bearer_token_provider(
                    credential,
                    "https://cognitiveservices.azure.com/.default",
                )
            raw_client = AsyncAzureOpenAI(**azure_kwargs)

        if settings.api_type == ApiType.RESPONSES:
            client = OpenAIChatClient(model=settings.model, async_client=raw_client)
        else:
            client = OpenAIChatCompletionClient(model=settings.model, async_client=raw_client)
        return ProviderBundle(
            client=client,
            route=route,
            raw_client=raw_client,
            _resources=[raw_client, credential],
        )


__all__ = ["HttpClientFactory", "ProviderBundle", "ProviderFactory", "RouteDescriptor"]
