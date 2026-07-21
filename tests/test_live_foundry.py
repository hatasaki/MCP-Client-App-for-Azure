from __future__ import annotations

import os

import pytest
from agent_framework import Agent

from app.agent_runtime import AgentRuntime
from app.foundry_config import FoundrySettings
from app.mcp_manager import MCPManager
from app.provider_factory import ProviderFactory
from app.session_manager import SessionManager

pytestmark = pytest.mark.live_foundry


def _enabled() -> bool:
    return os.environ.get("RUN_FOUNDRY_LIVE_TESTS") == "1"


def _project_settings() -> FoundrySettings:
    model = os.environ["FOUNDRY_MODEL"]
    return FoundrySettings.model_validate({
        "schemaVersion": 3,
        "endpointKind": "project",
        "endpoint": os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        "auth": {"type": "entra_id"},
        "agentInstructions": "Follow the user's requested output format exactly.",
        "apiProfiles": [{
            "apiType": "responses",
            "models": [model],
            "defaultModel": model,
            "versionMode": "v1",
            "options": {"maxOutputTokens": 64, "store": False},
        }],
        "defaultSelection": {"apiType": "responses", "model": model},
    })


@pytest.mark.skipif(not _enabled(), reason="Set RUN_FOUNDRY_LIVE_TESTS=1 to run cost-bearing Foundry tests.")
@pytest.mark.asyncio
async def test_project_responses_streaming_with_entra_id():
    settings = _project_settings()

    bundle = ProviderFactory().create(settings)
    agent = Agent(
        client=bundle.client,
        instructions=settings.agent_instructions,
        default_options=settings.to_maf_options(),
    )
    chunks: list[str] = []
    async with bundle, agent:
        stream = agent.run("Reply with exactly LIVE_OK and nothing else.", stream=True)
        async for update in stream:
            chunks.append(update.text)
        response = await stream.get_final_response()

    streamed = "".join(chunks).strip()
    assert streamed
    assert "LIVE_OK" in streamed
    assert "LIVE_OK" in response.text


@pytest.mark.skipif(not _enabled(), reason="Set RUN_FOUNDRY_LIVE_TESTS=1 to run cost-bearing Foundry tests.")
@pytest.mark.asyncio
async def test_application_runtime_streams_and_persists_project_response(tmp_path):
    settings = _project_settings()
    sessions = SessionManager(tmp_path)
    sessions.create("live-session")
    runtime = AgentRuntime(sessions, MCPManager([]))
    events = []

    async def capture(event, payload):
        events.append((event, payload))

    result = await runtime.run(
        session_id="live-session",
        message="Reply with exactly RUNTIME_OK and nothing else.",
        selected_tool_ids=[],
        settings=settings,
        emit=capture,
        request_id="live-runtime",
    )

    assert "RUNTIME_OK" in result.content
    assert events[0][0] == "chat:started"
    assert events[-1][0] == "chat:completed"
    assert any(event == "chat:delta" for event, _ in events)
    assert sessions.get("live-session")["messages"][-1]["status"] == "completed"
