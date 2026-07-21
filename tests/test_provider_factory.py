from __future__ import annotations

import pytest

from app.foundry_config import FoundrySettings
from app.provider_factory import ProviderFactory


def make_settings(**changes):
    payload = {
        "endpointKind": "model",
        "endpoint": "https://demo.services.ai.azure.com",
        "model": "deployment",
        "apiType": "responses",
        "versionMode": "v1",
        "auth": {"type": "api_key", "apiKey": "secret"},
        "options": {},
    }
    payload.update(changes)
    return FoundrySettings.model_validate(payload)


def test_project_route_contract():
    settings = make_settings(
        endpointKind="project",
        endpoint="https://demo.services.ai.azure.com/api/projects/project-one",
        auth={"type": "entra_id"},
    )

    route = ProviderFactory().describe_route(settings)

    assert route.provider == "foundry_responses"
    assert route.base_url == "https://demo.services.ai.azure.com/api/projects/project-one/openai/v1/"
    assert route.request_url == route.base_url + "responses"
    assert route.expected_auth_header == "Authorization"


@pytest.mark.parametrize(
    ("api_type", "operation"),
    [("responses", "responses"), ("chat_completions", "chat/completions")],
)
def test_model_v1_route_contract(api_type, operation):
    route = ProviderFactory().describe_route(make_settings(apiType=api_type))

    assert route.base_url == "https://demo.services.ai.azure.com/openai/v1/"
    assert route.request_url == route.base_url + operation
    assert route.expected_auth_header == "Authorization"


def test_model_dated_responses_route_contract():
    route = ProviderFactory().describe_route(make_settings(
        versionMode="dated",
        apiVersion="2025-04-01-preview",
    ))

    assert route.request_url == (
        "https://demo.services.ai.azure.com/openai/responses?api-version=2025-04-01-preview"
    )
    assert route.expected_auth_header == "api-key"


def test_model_dated_chat_route_contract():
    route = ProviderFactory().describe_route(make_settings(
        apiType="chat_completions",
        versionMode="dated",
        apiVersion="2025-04-01-preview",
        auth={"type": "entra_id"},
    ))

    assert route.request_url == (
        "https://demo.services.ai.azure.com/openai/deployments/deployment/chat/completions"
        "?api-version=2025-04-01-preview"
    )
    assert route.expected_auth_header == "Authorization"


def test_claude_route_contract():
    route = ProviderFactory().describe_route(make_settings(
        apiType="claude_messages",
        versionMode="provider",
        options={"maxTokens": 1024},
    ))

    assert route.provider == "anthropic_foundry"
    assert route.base_url == "https://demo.services.ai.azure.com/anthropic"
    assert route.request_url == "https://demo.services.ai.azure.com/anthropic/v1/messages?beta=true"


def test_resolved_non_default_model_controls_dated_deployment_route():
    settings = FoundrySettings.model_validate({
        "schemaVersion": 4,
        "endpointKind": "model",
        "endpoint": "https://demo.services.ai.azure.com",
        "auth": {"type": "entra_id"},
        "apiProfiles": [{
            "apiType": "chat_completions",
            "models": ["default-deployment", "selected-deployment"],
            "versionMode": "dated",
            "apiVersion": "2025-04-01-preview",
            "options": {},
        }],
        "defaultSelection": {
            "apiType": "chat_completions",
            "model": "default-deployment",
        },
    })

    route = ProviderFactory().describe_route(settings.resolve({
        "apiType": "chat_completions",
        "model": "selected-deployment",
    }))

    assert "/deployments/selected-deployment/chat/completions" in route.request_url
    assert "default-deployment" not in route.request_url


@pytest.mark.asyncio
async def test_api_key_provider_bundles_close():
    factory = ProviderFactory()
    for settings in (
        make_settings(),
        make_settings(apiType="chat_completions"),
        make_settings(apiType="claude_messages", versionMode="provider", options={"maxTokens": 128}),
    ):
        bundle = factory.create(settings)
        assert bundle.client is not None
        await bundle.close()
        await bundle.close()
