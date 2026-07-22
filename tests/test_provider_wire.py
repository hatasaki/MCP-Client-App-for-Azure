from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from agent_framework import Content, FunctionTool, Message
from azure.core.credentials import AccessToken

from app.foundry_config import FoundrySettings
from app.provider_factory import ProviderFactory


def make_settings(**changes: Any) -> FoundrySettings:
    payload: dict[str, Any] = {
        "endpointKind": "model",
        "endpoint": "https://wire.services.ai.azure.com",
        "model": "deployment",
        "apiType": "responses",
        "versionMode": "v1",
        "auth": {"type": "api_key", "apiKey": "wire-secret"},
        "options": {},
    }
    payload.update(changes)
    return FoundrySettings.model_validate(payload)


class WireCapture:
    def __init__(self, response: dict[str, Any]):
        self.response = response
        self.requests: list[httpx.Request] = []
        self.bodies: list[dict[str, Any]] = []

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.bodies.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json=self.response, request=request)

    def factory(self, _settings: FoundrySettings, _route: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle))


def noop_tool() -> FunctionTool:
    return FunctionTool(name="noop", description="No operation", func=lambda: "ok")


class FakeCredential:
    def __init__(self):
        self.scopes: list[str] = []
        self.closed = False

    async def get_token(self, *scopes: str, **_kwargs: Any) -> AccessToken:
        self.scopes.extend(scopes)
        return AccessToken("wire-entra-token", 9_999_999_999)

    async def close(self) -> None:
        self.closed = True


def openai_response() -> dict[str, Any]:
    return {
        "id": "resp_wire",
        "object": "response",
        "created_at": 1,
        "status": "completed",
        "model": "deployment",
        "output": [{
            "id": "msg_wire",
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "ok", "annotations": []}],
        }],
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
        "temperature": 0,
        "top_p": 1,
        "truncation": "disabled",
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    }


def chat_response() -> dict[str, Any]:
    return {
        "id": "chatcmpl_wire",
        "object": "chat.completion",
        "created": 1,
        "model": "deployment",
        "choices": [{
            "index": 0,
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": "ok"},
        }],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def anthropic_response() -> dict[str, Any]:
    return {
        "id": "msg_wire",
        "type": "message",
        "role": "assistant",
        "model": "claude-deployment",
        "content": [{"type": "text", "text": "ok"}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


@pytest.mark.asyncio
async def test_v1_responses_maf_wire_url_auth_and_explicit_options():
    capture = WireCapture(openai_response())
    settings = make_settings(options={
        "temperature": 0,
        "maxOutputTokens": 23,
        "reasoningEffort": "none",
        "reasoningSummary": "detailed",
        "verbosity": "high",
        "store": False,
        "parallelToolCalls": False,
        "serviceTier": "priority",
        "truncation": "auto",
        "maxToolCalls": 2,
        "safetyIdentifier": "wire-safety",
        "promptCacheKey": "wire-cache",
        "metadata": {"suite": "wire"},
    })
    bundle = ProviderFactory(http_client_factory=capture.factory).create(settings)

    async with bundle:
        options = settings.to_maf_options()
        options["tools"] = [noop_tool()]
        response = await bundle.client.get_response(
            [Message(role="user", contents=["hello"])],
            options=options,
        )

    request = capture.requests[0]
    body = capture.bodies[0]
    assert str(request.url) == "https://wire.services.ai.azure.com/openai/v1/responses"
    assert request.headers["authorization"] == "Bearer wire-secret"
    assert "api-key" not in request.headers
    assert body["model"] == "deployment"
    assert body["input"][0]["content"] == [{"type": "input_text", "text": "hello"}]
    assert body["temperature"] == 0
    assert body["max_output_tokens"] == 23
    assert body["reasoning"] == {"effort": "none", "summary": "detailed"}
    assert body["text"] == {"verbosity": "high"}
    assert body["store"] is False
    assert body["parallel_tool_calls"] is False
    assert body["service_tier"] == "priority"
    assert body["truncation"] == "auto"
    assert body["max_tool_calls"] == 2
    assert body["safety_identifier"] == "wire-safety"
    assert body["prompt_cache_key"] == "wire-cache"
    assert body["metadata"] == {"suite": "wire"}
    assert "top_p" not in body
    assert response.text == "ok"


@pytest.mark.asyncio
async def test_dated_chat_maf_wire_url_api_key_and_body():
    capture = WireCapture(chat_response())
    settings = make_settings(
        apiType="chat_completions",
        versionMode="dated",
        apiVersion="2025-04-01-preview",
        options={
            "temperature": 0,
            "maxCompletionTokens": 31,
            "reasoningEffort": "low",
            "verbosity": "high",
            "stop": ["END"],
            "seed": 3,
            "frequencyPenalty": 0.2,
            "presencePenalty": -0.1,
            "logprobs": False,
            "topLogprobs": 0,
            "parallelToolCalls": False,
            "store": False,
            "serviceTier": "priority",
            "safetyIdentifier": "wire-safety",
            "promptCacheKey": "wire-cache",
            "metadata": {"suite": "wire"},
        },
    )
    bundle = ProviderFactory(http_client_factory=capture.factory).create(settings)

    async with bundle:
        options = settings.to_maf_options()
        options["tools"] = [noop_tool()]
        response = await bundle.client.get_response(
            [Message(role="user", contents=["hello"])],
            options=options,
        )

    request = capture.requests[0]
    body = capture.bodies[0]
    assert str(request.url) == (
        "https://wire.services.ai.azure.com/openai/deployments/deployment/chat/completions"
        "?api-version=2025-04-01-preview"
    )
    assert request.headers["api-key"] == "wire-secret"
    assert body["model"] == "deployment"
    assert body["messages"][0]["content"] == "hello"
    assert body["temperature"] == 0
    assert body["max_completion_tokens"] == 31
    assert body["reasoning_effort"] == "low"
    assert body["verbosity"] == "high"
    assert body["stop"] == ["END"]
    assert body["seed"] == 3
    assert body["frequency_penalty"] == 0.2
    assert body["presence_penalty"] == -0.1
    assert body["logprobs"] is False
    assert body["top_logprobs"] == 0
    assert body["parallel_tool_calls"] is False
    assert body["store"] is False
    assert body["service_tier"] == "priority"
    assert body["safety_identifier"] == "wire-safety"
    assert body["prompt_cache_key"] == "wire-cache"
    assert body["metadata"] == {"suite": "wire"}
    assert "top_p" not in body
    assert response.text == "ok"


@pytest.mark.asyncio
async def test_claude_foundry_maf_wire_url_auth_and_body():
    capture = WireCapture(anthropic_response())
    settings = make_settings(
        model="claude-deployment",
        apiType="claude_messages",
        versionMode="provider",
        options={
            "maxTokens": 47,
            "temperature": 0,
            "topK": 3,
            "stopSequences": ["END"],
            "thinking": {"type": "adaptive"},
            "effort": "high",
            "serviceTier": "auto",
            "parallelToolUse": False,
            "metadataUserId": "wire-user",
        },
    )
    bundle = ProviderFactory(http_client_factory=capture.factory).create(settings)

    async with bundle:
        options = settings.to_maf_options()
        options["tools"] = [noop_tool()]
        response = await bundle.client.get_response(
            [Message(role="user", contents=["hello"])],
            options=options,
        )

    request = capture.requests[0]
    body = capture.bodies[0]
    assert str(request.url) == "https://wire.services.ai.azure.com/anthropic/v1/messages?beta=true"
    assert request.headers["api-key"] == "wire-secret"
    assert body["model"] == "claude-deployment"
    assert body["messages"][0]["content"] == [{"type": "text", "text": "hello"}]
    assert body["max_tokens"] == 47
    assert body["temperature"] == 0
    assert body["top_k"] == 3
    assert body["stop_sequences"] == ["END"]
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"] == {"effort": "high"}
    assert body["service_tier"] == "auto"
    assert body["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert body["metadata"] == {"user_id": "wire-user"}
    assert "top_p" not in body
    assert response.text == "ok"


@pytest.mark.asyncio
async def test_responses_attachment_wire_uses_native_pdf_input_file_and_image():
    capture = WireCapture(openai_response())
    settings = make_settings()
    bundle = ProviderFactory(http_client_factory=capture.factory).create(settings)

    async with bundle:
        await bundle.client.get_response(
            [Message(role="user", contents=[
                Content.from_data(
                    b"%PDF-1.4\n%%EOF",
                    "application/pdf",
                    additional_properties={"filename": "report.pdf"},
                ),
                Content.from_data(b"\x89PNG\r\n\x1a\nimage", "image/png"),
                "Analyze both files.",
            ])],
            options=settings.to_maf_options(),
        )

    content = capture.bodies[0]["input"][0]["content"]
    assert content[0] == {
        "type": "input_file",
        "file_data": "data:application/pdf;base64,JVBERi0xLjQKJSVFT0Y=",
        "filename": "report.pdf",
    }
    assert content[1]["type"] == "input_image"
    assert content[1]["image_url"].startswith("data:image/png;base64,")
    assert content[2] == {"type": "input_text", "text": "Analyze both files."}


@pytest.mark.asyncio
async def test_chat_completions_attachment_wire_uses_image_url():
    capture = WireCapture(chat_response())
    settings = make_settings(
        apiType="chat_completions",
        versionMode="dated",
        apiVersion="2025-04-01-preview",
    )
    bundle = ProviderFactory(http_client_factory=capture.factory).create(settings)

    async with bundle:
        await bundle.client.get_response(
            [Message(role="user", contents=[
                Content.from_data(b"\x89PNG\r\n\x1a\nimage", "image/png"),
                "Describe the image.",
            ])],
            options=settings.to_maf_options(),
        )

    messages = capture.bodies[0]["messages"]
    assert messages[0]["content"][0]["type"] == "image_url"
    assert messages[0]["content"][0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert messages[1] == {"role": "user", "content": "Describe the image."}


@pytest.mark.asyncio
async def test_claude_attachment_wire_uses_base64_image_block():
    capture = WireCapture(anthropic_response())
    settings = make_settings(
        model="claude-deployment",
        apiType="claude_messages",
        versionMode="provider",
        options={"maxTokens": 47},
    )
    bundle = ProviderFactory(http_client_factory=capture.factory).create(settings)

    async with bundle:
        await bundle.client.get_response(
            [Message(role="user", contents=[
                Content.from_data(b"\xff\xd8\xffimage", "image/jpeg"),
                "Describe the image.",
            ])],
            options=settings.to_maf_options(),
        )

    content = capture.bodies[0]["messages"][0]["content"]
    assert content[0]["type"] == "image"
    assert content[0]["source"] == {
        "data": "/9j/aW1hZ2U=",
        "media_type": "image/jpeg",
        "type": "base64",
    }
    assert content[1] == {"type": "text", "text": "Describe the image."}


@pytest.mark.parametrize(
    ("settings", "response_body", "expected_url", "expected_scope"),
    [
        (
            make_settings(auth={"type": "entra_id"}),
            openai_response(),
            "https://wire.services.ai.azure.com/openai/v1/responses",
            "https://ai.azure.com/.default",
        ),
        (
            make_settings(
                apiType="chat_completions",
                versionMode="dated",
                apiVersion="2025-04-01-preview",
                auth={"type": "entra_id"},
            ),
            chat_response(),
            "https://wire.services.ai.azure.com/openai/deployments/deployment/chat/completions"
            "?api-version=2025-04-01-preview",
            "https://cognitiveservices.azure.com/.default",
        ),
        (
            make_settings(
                model="claude-deployment",
                apiType="claude_messages",
                versionMode="provider",
                auth={"type": "entra_id"},
                options={"maxTokens": 47},
            ),
            anthropic_response(),
            "https://wire.services.ai.azure.com/anthropic/v1/messages?beta=true",
            "https://ai.azure.com/.default",
        ),
    ],
)
@pytest.mark.asyncio
async def test_model_entra_wire_uses_bearer_token_and_expected_scope(
    settings: FoundrySettings,
    response_body: dict[str, Any],
    expected_url: str,
    expected_scope: str,
):
    capture = WireCapture(response_body)
    credential = FakeCredential()
    bundle = ProviderFactory(
        credential_factory=lambda: credential,
        http_client_factory=capture.factory,
    ).create(settings)

    async with bundle:
        response = await bundle.client.get_response(
            [Message(role="user", contents=["hello"])],
            options=settings.to_maf_options(),
        )

    request = capture.requests[0]
    assert str(request.url) == expected_url
    assert request.headers["authorization"] == "Bearer wire-entra-token"
    assert "api-key" not in request.headers
    assert credential.scopes == [expected_scope]
    assert credential.closed is True
    assert response.text == "ok"
