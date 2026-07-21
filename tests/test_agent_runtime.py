from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from agent_framework import (
    AgentResponseUpdate,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    Message,
    ResponseStream,
)

from app.agent_runtime import AgentRunBusyError, AgentRuntime
from app.foundry_config import FoundrySettings
from app.mcp_manager import MCPManager
from app.provider_factory import ProviderBundle, RouteDescriptor
from app.session_manager import SessionManager


def settings(
    instructions: str = "New instructions",
    models: list[str] | None = None,
) -> FoundrySettings:
    deployments = models or ["deployment"]
    return FoundrySettings.model_validate({
        "schemaVersion": 4,
        "endpointKind": "model",
        "endpoint": "https://example.openai.azure.com",
        "auth": {"type": "api_key", "apiKey": "secret"},
        "agentInstructions": instructions,
        "apiProfiles": [{
            "apiType": "responses",
            "models": deployments,
            "versionMode": "v1",
            "options": {"store": False},
        }],
        "defaultSelection": {"apiType": "responses", "model": deployments[0]},
    })


class FakeStreamingClient:
    STORES_BY_DEFAULT = False

    def __init__(self, chunks: list[str], gate: asyncio.Event | None = None):
        self.model = "fake"
        self.chunks = chunks
        self.gate = gate
        self.calls: list[tuple[list[Message], dict[str, Any]]] = []

    def get_response(self, messages, *, stream=False, options=None, **kwargs):
        normalized = list(messages)
        self.calls.append((normalized, dict(options or {})))
        if not stream:
            async def response():
                return ChatResponse(messages=Message("assistant", ["" ]))
            return response()

        async def updates():
            for index, chunk in enumerate(self.chunks):
                yield ChatResponseUpdate(
                    contents=[Content.from_text(text=chunk)],
                    role="assistant",
                    response_id="resp_fake",
                    message_id="msg_fake",
                )
                if index == 0 and self.gate is not None:
                    await self.gate.wait()

        def finalize(items):
            text = "".join(item.text for item in items)
            return ChatResponse(
                messages=Message("assistant", [text]),
                response_id="resp_fake",
            )

        return ResponseStream(updates(), finalizer=finalize)


class FakeProviderFactory:
    def __init__(self, client):
        self.client = client

    def create(self, _settings):
        return ProviderBundle(
            client=self.client,
            route=RouteDescriptor(
                provider="fake",
                base_url="https://example.test/",
                request_url="https://example.test/responses",
                auth_type=_settings.auth.type,
                expected_auth_header="api-key",
            ),
        )


class BrokenProviderFactory:
    def create(self, _settings):
        raise RuntimeError("provider construction failed")


@pytest.mark.asyncio
async def test_runtime_streams_and_persists_completed_message(tmp_path: Path):
    sessions = SessionManager(tmp_path)
    sessions.create("chat")
    mcp = MCPManager([])
    client = FakeStreamingClient(["Hello", " world"])
    runtime = AgentRuntime(sessions, mcp, provider_factory=FakeProviderFactory(client))
    events: list[tuple[str, dict[str, Any]]] = []

    result = await runtime.run(
        session_id="chat",
        message="Hi",
        selected_tool_ids=[],
        settings=settings(),
        emit=lambda event, payload: _capture(events, event, payload),
        request_id="run-1",
    )

    assert result.content == "Hello world"
    assert [name for name, _ in events] == [
        "chat:started",
        "chat:delta",
        "chat:delta",
        "chat:completed",
    ]
    assert [event[1]["sequence"] for event in events] == [1, 2, 3, 4]
    assert events[0][1]["modelSelection"] == {
        "apiType": "responses",
        "model": "deployment",
    }
    public = sessions.get("chat")
    assert public["messages"][-1]["status"] == "completed"
    assert public["messages"][-1]["content"] == "Hello world"
    assert "mafState" not in public


@pytest.mark.asyncio
async def test_provider_construction_failure_closes_streaming_message(tmp_path: Path):
    sessions = SessionManager(tmp_path)
    sessions.create("chat")
    runtime = AgentRuntime(sessions, MCPManager([]), provider_factory=BrokenProviderFactory())
    events: list[tuple[str, dict[str, Any]]] = []

    with pytest.raises(RuntimeError, match="provider construction failed"):
        await runtime.run(
            session_id="chat",
            message="hello",
            selected_tool_ids=[],
            settings=settings(),
            emit=lambda event, payload: _capture(events, event, payload),
        )

    assistant = sessions.get("chat")["messages"][-1]
    assert assistant["status"] == "error"
    assert events[-1][0] == "chat:error"


@pytest.mark.asyncio
async def test_runtime_cancel_retains_partial_display_and_rolls_back(tmp_path: Path):
    sessions = SessionManager(tmp_path)
    sessions.create("chat")
    mcp = MCPManager([])
    gate = asyncio.Event()
    client = FakeStreamingClient(["partial", "never"], gate)
    runtime = AgentRuntime(sessions, mcp, provider_factory=FakeProviderFactory(client))
    events: list[tuple[str, dict[str, Any]]] = []

    task = asyncio.create_task(runtime.run(
        session_id="chat",
        message="Long answer",
        selected_tool_ids=[],
        settings=settings(),
        emit=lambda event, payload: _capture(events, event, payload),
        request_id="cancel-me",
    ))
    while not any(name == "chat:delta" for name, _ in events):
        await asyncio.sleep(0)

    assert await runtime.cancel("chat", "cancel-me") is True
    with pytest.raises(asyncio.CancelledError):
        await task

    assistant = sessions.get("chat")["messages"][-1]
    assert assistant["status"] == "cancelled"
    assert assistant["content"] == "partial"
    assert any(name == "chat:cancelled" for name, _ in events)


@pytest.mark.asyncio
async def test_cancellation_during_started_event_never_leaves_streaming_message(tmp_path: Path):
    sessions = SessionManager(tmp_path)
    sessions.create("chat")
    runtime = AgentRuntime(
        sessions,
        MCPManager([]),
        provider_factory=FakeProviderFactory(FakeStreamingClient(["unused"])),
    )
    events: list[tuple[str, dict[str, Any]]] = []

    async def cancel_at_started(event: str, payload: dict[str, Any]):
        events.append((event, payload))
        if event == "chat:started":
            asyncio.current_task().cancel()
            await asyncio.sleep(0)

    with pytest.raises(asyncio.CancelledError):
        await runtime.run(
            session_id="chat",
            message="hello",
            selected_tool_ids=[],
            settings=settings(),
            emit=cancel_at_started,
        )

    assistant = sessions.get("chat")["messages"][-1]
    assert assistant["status"] == "cancelled"
    assert sessions.get_session("chat")["mafState"] is None
    assert [event for event, _ in events] == ["chat:started", "chat:cancelled"]


@pytest.mark.asyncio
async def test_cancellation_during_completed_event_never_downgrades_committed_state(tmp_path: Path):
    sessions = SessionManager(tmp_path)
    sessions.create("chat")
    runtime = AgentRuntime(
        sessions,
        MCPManager([]),
        provider_factory=FakeProviderFactory(FakeStreamingClient(["complete"])),
    )

    async def cancel_at_completed(event: str, _payload: dict[str, Any]):
        if event == "chat:completed":
            asyncio.current_task().cancel()
            await asyncio.sleep(0)

    with pytest.raises(asyncio.CancelledError):
        await runtime.run(
            session_id="chat",
            message="hello",
            selected_tool_ids=[],
            settings=settings(),
            emit=cancel_at_completed,
        )

    internal = sessions.get_session("chat")
    assistant = internal["messages"][-1]
    assert assistant["status"] == "completed"
    assert assistant["content"] == "complete"
    assert isinstance(internal["mafState"], dict)
    assert internal["preRunMafState"] is None


@pytest.mark.asyncio
async def test_runtime_rejects_second_run_for_same_session(tmp_path: Path):
    sessions = SessionManager(tmp_path)
    sessions.create("chat")
    gate = asyncio.Event()
    runtime = AgentRuntime(
        sessions,
        MCPManager([]),
        provider_factory=FakeProviderFactory(FakeStreamingClient(["wait"], gate)),
    )
    events: list[tuple[str, dict[str, Any]]] = []
    first = asyncio.create_task(runtime.run(
        session_id="chat",
        message="first",
        selected_tool_ids=[],
        settings=settings(),
        emit=lambda event, payload: _capture(events, event, payload),
    ))
    while not any(name == "chat:delta" for name, _ in events):
        await asyncio.sleep(0)

    with pytest.raises(AgentRunBusyError):
        await runtime.run(
            session_id="chat",
            message="second",
            selected_tool_ids=[],
            settings=settings(),
            emit=lambda event, payload: _capture(events, event, payload),
        )
    await runtime.cancel("chat")
    with pytest.raises(asyncio.CancelledError):
        await first


@pytest.mark.asyncio
async def test_invalid_selected_tool_is_rejected_before_session_mutation(tmp_path: Path):
    sessions = SessionManager(tmp_path)
    sessions.create("chat")
    runtime = AgentRuntime(
        sessions,
        MCPManager([]),
        provider_factory=FakeProviderFactory(FakeStreamingClient(["unused"])),
    )
    events: list[tuple[str, dict[str, Any]]] = []

    with pytest.raises(KeyError, match="missing:tool"):
        await runtime.run(
            session_id="chat",
            message="hello",
            selected_tool_ids=["missing:tool"],
            settings=settings(),
            emit=lambda event, payload: _capture(events, event, payload),
        )

    assert sessions.get("chat")["messages"] == []
    assert sessions.get("chat")["selectedModel"] is None
    assert events == []


def test_runtime_error_message_redacts_credentials():
    message = AgentRuntime._safe_error_message(
        RuntimeError("api-key=secret-value Authorization: Bearer token-value"),
        "secret-value",
    )

    assert "secret-value" not in message
    assert "token-value" not in message
    assert message.count("[REDACTED]") == 2


def test_safe_json_parses_object_strings_and_preserves_plain_text():
    assert AgentRuntime._safe_json('{"value": 1}') == {"value": 1}
    assert AgentRuntime._safe_json("plain text") == "plain text"
    assert AgentRuntime._safe_json({"value": 1}) == {"value": 1}


@pytest.mark.asyncio
async def test_approval_batch_defaults_missing_decisions_to_denied(tmp_path: Path):
    sessions = SessionManager(tmp_path)
    sessions.create("chat")
    runtime = AgentRuntime(sessions, MCPManager([]), provider_factory=FakeProviderFactory(FakeStreamingClient([])))
    call_one = Content.from_function_call(call_id="call-1", name="one", arguments={"x": 1})
    call_two = Content.from_function_call(call_id="call-2", name="two", arguments={"x": 2})
    requests = [
        Content.from_function_approval_request(id="approval-1", function_call=call_one),
        Content.from_function_approval_request(id="approval-2", function_call=call_two),
    ]
    events: list[tuple[str, dict[str, Any]]] = []

    pending = asyncio.create_task(runtime._request_approval(
        run_request_id="run",
        session_id="chat",
        requests=requests,
        auto_approve=False,
        send=lambda event, payload=None: _capture(events, event, dict(payload or {})),
    ))
    while "run" not in runtime._pending_approvals:
        await asyncio.sleep(0)
    runtime.resolve_approval("run", [{"requestId": "approval-1", "approved": True}])

    decisions, approve_all = await pending
    assert decisions == {"approval-1": True, "approval-2": False}
    assert approve_all is False
    assert events[0][0] == "chat:approval-required"
    assert len(events[0][1]["requests"]) == 2


@pytest.mark.parametrize(
    "decisions",
    [
        [{"id": "approval-1", "approved": True}],
        [{"requestId": "approval-1", "approved": "true"}],
        [
            {"requestId": "approval-1", "approved": True},
            {"requestId": "approval-1", "approved": False},
        ],
        [{"requestId": "unknown", "approved": True}],
    ],
)
@pytest.mark.asyncio
async def test_approval_rejects_malformed_or_unknown_decisions(tmp_path: Path, decisions):
    sessions = SessionManager(tmp_path)
    sessions.create("chat")
    runtime = AgentRuntime(sessions, MCPManager([]), provider_factory=FakeProviderFactory(FakeStreamingClient([])))
    request = Content.from_function_approval_request(
        id="approval-1",
        function_call=Content.from_function_call(call_id="call-1", name="one", arguments={}),
    )
    pending = asyncio.create_task(runtime._request_approval(
        run_request_id="run",
        session_id="chat",
        requests=[request],
        auto_approve=False,
        send=lambda event, payload=None: _capture([], event, dict(payload or {})),
    ))
    while "run" not in runtime._pending_approvals:
        await asyncio.sleep(0)

    with pytest.raises(ValueError):
        runtime.resolve_approval("run", decisions)

    runtime.resolve_approval("run", [])
    assert await pending == ({"approval-1": False}, False)


@pytest.mark.asyncio
async def test_approval_assigns_missing_request_id(tmp_path: Path):
    sessions = SessionManager(tmp_path)
    sessions.create("chat")
    runtime = AgentRuntime(sessions, MCPManager([]), provider_factory=FakeProviderFactory(FakeStreamingClient([])))
    request = Content.from_function_approval_request(
        id=None,
        function_call=Content.from_function_call(call_id="call-1", name="one", arguments={}),
    )
    events: list[tuple[str, dict[str, Any]]] = []
    pending = asyncio.create_task(runtime._request_approval(
        run_request_id="run",
        session_id="chat",
        requests=[request],
        auto_approve=False,
        send=lambda event, payload=None: _capture(events, event, dict(payload or {})),
    ))
    while "run" not in runtime._pending_approvals:
        await asyncio.sleep(0)

    generated_id = events[0][1]["requests"][0]["id"]
    assert generated_id
    runtime.resolve_approval("run", [{"requestId": generated_id, "approved": True}])
    assert await pending == ({generated_id: True}, False)


@pytest.mark.asyncio
async def test_settings_change_replays_completed_text_under_new_instructions(tmp_path: Path):
    sessions = SessionManager(tmp_path)
    sessions.create("chat")
    sessions.append_message("chat", role="user", content="old question")
    sessions.append_message("chat", role="assistant", content="old answer")
    sessions.append_message("chat", role="assistant", content="cancelled", status="cancelled")
    # Establish an old fingerprint and opaque state.
    from agent_framework import AgentSession
    sessions.save_agent_session("chat", AgentSession(session_id="chat", service_session_id="resp_old"), "old")

    client = FakeStreamingClient(["new answer"])
    runtime = AgentRuntime(
        sessions,
        MCPManager([]),
        provider_factory=FakeProviderFactory(client),
    )
    events: list[tuple[str, dict[str, Any]]] = []

    await runtime.run(
        session_id="chat",
        message="new question",
        selected_tool_ids=[],
        settings=settings("Brand new instructions"),
        emit=lambda event, payload: _capture(events, event, payload),
    )

    sent_messages, sent_options = client.calls[0]
    sent_text = [(message.role, message.text) for message in sent_messages]
    assert sent_options["instructions"] == "Brand new instructions"
    assert ("user", "old question") in sent_text
    assert ("assistant", "old answer") in sent_text
    assert ("assistant", "cancelled") not in sent_text
    assert ("user", "new question") in sent_text
    assert events[0][1]["stateReset"] is True


@pytest.mark.asyncio
async def test_model_change_rebuilds_state_and_replays_completed_text(tmp_path: Path):
    sessions = SessionManager(tmp_path)
    sessions.create("chat", {"apiType": "responses", "model": "primary"})
    configured = settings(models=["primary", "secondary"])
    client = FakeStreamingClient(["answer"])
    runtime = AgentRuntime(
        sessions,
        MCPManager([]),
        provider_factory=FakeProviderFactory(client),
    )

    first_events: list[tuple[str, dict[str, Any]]] = []
    await runtime.run(
        session_id="chat",
        message="first question",
        selected_tool_ids=[],
        settings=configured.resolve({"apiType": "responses", "model": "primary"}),
        emit=lambda event, payload: _capture(first_events, event, payload),
    )

    second_events: list[tuple[str, dict[str, Any]]] = []
    await runtime.run(
        session_id="chat",
        message="second question",
        selected_tool_ids=[],
        settings=configured.resolve({"apiType": "responses", "model": "secondary"}),
        emit=lambda event, payload: _capture(second_events, event, payload),
    )

    replayed = [(message.role, message.text) for message in client.calls[1][0]]
    assert ("user", "first question") in replayed
    assert ("assistant", "answer") in replayed
    assert ("user", "second question") in replayed
    assert second_events[0][1]["stateReset"] is True
    assert second_events[0][1]["modelSelection"]["model"] == "secondary"
    assert sessions.get("chat")["selectedModel"]["model"] == "secondary"


@pytest.mark.asyncio
async def test_model_selection_cannot_change_while_run_lock_is_active(tmp_path: Path):
    sessions = SessionManager(tmp_path)
    sessions.create("chat", {"apiType": "responses", "model": "primary"})
    configured = settings(models=["primary", "secondary"])
    gate = asyncio.Event()
    events: list[tuple[str, dict[str, Any]]] = []
    runtime = AgentRuntime(
        sessions,
        MCPManager([]),
        provider_factory=FakeProviderFactory(FakeStreamingClient(["partial", "done"], gate)),
    )
    task = asyncio.create_task(runtime.run(
        session_id="chat",
        message="question",
        selected_tool_ids=[],
        settings=configured.resolve({"apiType": "responses", "model": "primary"}),
        emit=lambda event, payload: _capture(events, event, payload),
    ))
    while not any(event == "chat:delta" for event, _ in events):
        await asyncio.sleep(0)

    with pytest.raises(AgentRunBusyError):
        await runtime.set_selected_model(
            "chat",
            configured.resolve({"apiType": "responses", "model": "secondary"}).selection,
        )
    assert sessions.get("chat")["selectedModel"]["model"] == "primary"

    await runtime.cancel("chat")
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_settings_reconciliation_cannot_remove_active_run_model(tmp_path: Path):
    sessions = SessionManager(tmp_path)
    sessions.create("chat", {"apiType": "responses", "model": "secondary"})
    configured = settings(models=["primary", "secondary"])
    replacement = settings(models=["primary"])
    gate = asyncio.Event()
    events: list[tuple[str, dict[str, Any]]] = []
    runtime = AgentRuntime(
        sessions,
        MCPManager([]),
        provider_factory=FakeProviderFactory(FakeStreamingClient(["partial", "done"], gate)),
    )
    task = asyncio.create_task(runtime.run(
        session_id="chat",
        message="question",
        selected_tool_ids=[],
        settings=configured.resolve({"apiType": "responses", "model": "secondary"}),
        emit=lambda event, payload: _capture(events, event, payload),
    ))
    while not any(event == "chat:delta" for event, _ in events):
        await asyncio.sleep(0)

    persisted = False

    def persist():
        nonlocal persisted
        persisted = True
        return replacement

    with pytest.raises(AgentRunBusyError, match="cannot remove"):
        await runtime.apply_settings_update(replacement, persist)
    assert persisted is False
    assert sessions.get("chat")["selectedModel"]["model"] == "secondary"

    await runtime.cancel("chat")
    with pytest.raises(asyncio.CancelledError):
        await task


async def _capture(events: list[tuple[str, dict[str, Any]]], event: str, payload: dict[str, Any]):
    events.append((event, payload))
