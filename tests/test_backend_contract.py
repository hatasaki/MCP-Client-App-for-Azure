from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import backend.main as backend
from app.foundry_config import FoundrySettings


def project_settings() -> FoundrySettings:
    return FoundrySettings.model_validate({
        "endpointKind": "project",
        "endpoint": "https://example.services.ai.azure.com/api/projects/demo",
        "model": "deployment",
        "apiType": "responses",
        "versionMode": "v1",
        "auth": {"type": "entra_id"},
        "options": {},
    })


def project_write_payload() -> dict[str, Any]:
    return {
        "schemaVersion": 2,
        "endpointKind": "project",
        "endpoint": "https://example.services.ai.azure.com/api/projects/demo",
        "model": "deployment",
        "apiType": "responses",
        "versionMode": "v1",
        "auth": {"type": "entra_id", "apiKey": {"action": "clear"}},
        "agentInstructions": "Test instructions",
        "options": {"store": False},
    }


def test_foundry_settings_rest_contract_and_secret_redaction(monkeypatch):
    state: dict[str, FoundrySettings | None] = {"settings": None}

    def update(payload):
        state["settings"] = payload.resolve(state["settings"])
        return state["settings"]

    monkeypatch.setattr(backend, "load_foundry_settings", lambda: None)
    monkeypatch.setattr(backend, "update_foundry_settings", update)
    monkeypatch.setattr(backend, "foundry_settings", None)

    with TestClient(backend.fastapi_app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/foundry-settings/status").json() == {
            "isConfigured": False,
            "schemaVersion": 2,
        }
        assert client.get("/foundry-settings").status_code == 404

        response = client.put("/foundry-settings", json=project_write_payload())
        assert response.status_code == 200
        body = response.json()
        assert body["endpointKind"] == "project"
        assert body["auth"] == {"type": "entra_id", "apiKeyConfigured": False}
        assert '"apiKey":' not in response.text
        assert client.get("/foundry-settings").json() == body
        assert client.get("/foundry-settings/status").json()["isConfigured"] is True


def test_foundry_settings_rest_rejects_project_api_key(monkeypatch):
    monkeypatch.setattr(backend, "load_foundry_settings", lambda: None)
    monkeypatch.setattr(backend, "update_foundry_settings", lambda payload: payload.resolve(None))
    monkeypatch.setattr(backend, "foundry_settings", None)
    payload = project_write_payload()
    payload["auth"] = {
        "type": "api_key",
        "apiKey": {"action": "set", "value": "must-not-echo"},
    }

    with TestClient(backend.fastapi_app) as client:
        response = client.put("/foundry-settings", json=payload)

    assert response.status_code == 422
    assert "Project endpoints use Entra ID" in response.text
    assert "must-not-echo" not in response.text


@pytest.mark.asyncio
async def test_adapter_level_chat_error_has_complete_terminal_contract(monkeypatch):
    events: list[tuple[str, dict[str, Any], str | None]] = []

    async def capture(event: str, payload: dict[str, Any], room: str | None = None, **_kwargs: Any):
        events.append((event, payload, room))

    monkeypatch.setattr(backend.sio, "emit", capture)
    monkeypatch.setattr(backend, "foundry_settings", None)

    await backend.chat_send("socket-1", {
        "requestId": "request-1",
        "sessionId": "session-1",
        "message": "hello",
    })

    event, payload, room = events[-1]
    assert event == "chat:error"
    assert room == "socket-1"
    assert payload == {
        "requestId": "request-1",
        "sessionId": "session-1",
        "messageId": "",
        "epoch": 0,
        "sequence": 1,
        "code": "SettingsMissing",
        "message": "Microsoft Foundry settings are not configured.",
        "content": "",
    }


@pytest.mark.asyncio
async def test_unhandled_runtime_failure_emits_terminal_fallback_and_closes_message(monkeypatch):
    events: list[tuple[str, dict[str, Any], str | None]] = []
    session_id = f"backend-{uuid4()}"
    backend.session_manager.create(session_id)

    async def capture(event: str, payload: dict[str, Any], room: str | None = None, **_kwargs: Any):
        events.append((event, payload, room))

    class BrokenRuntime:
        async def run(self, *, session_id: str, emit, request_id: str, **_kwargs: Any):
            message = backend.session_manager.append_message(
                session_id,
                role="assistant",
                content="partial",
                status="streaming",
            )
            await emit("chat:started", {
                "requestId": request_id,
                "sessionId": session_id,
                "messageId": message["id"],
                "epoch": 4,
                "sequence": 7,
                "userMessageId": "user-message",
                "stateReset": False,
            })
            raise RuntimeError("sensitive provider detail")

    monkeypatch.setattr(backend.sio, "emit", capture)
    monkeypatch.setattr(backend, "foundry_settings", project_settings())
    monkeypatch.setattr(backend, "agent_runtime", BrokenRuntime())

    await backend.chat_send("socket-2", {
        "requestId": "request-2",
        "sessionId": session_id,
        "message": "hello",
        "selectedToolIds": [],
    })

    errors = [payload for event, payload, _ in events if event == "chat:error"]
    assert len(errors) == 1
    error = errors[0]
    assert error["requestId"] == "request-2"
    assert error["sessionId"] == session_id
    assert error["messageId"]
    assert error["epoch"] == 4
    assert error["sequence"] == 8
    assert error["code"] == "RuntimeError"
    assert error["content"] == "partial"
    assert "sensitive provider detail" not in error["message"]
    assert error["session"]["messages"][-1]["status"] == "error"
    assert backend.session_manager.get(session_id)["messages"][-1]["status"] == "error"
