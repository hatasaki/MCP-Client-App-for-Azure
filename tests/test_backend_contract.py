from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import backend.main as backend
from app.agent_runtime import AgentRunBusyError
from app.foundry_config import FoundrySettings, FoundrySettingsWrite
from app.secret_protection import SecretProtectionError


def project_settings() -> FoundrySettings:
    return FoundrySettings.model_validate({
        "schemaVersion": 3,
        "endpointKind": "project",
        "endpoint": "https://example.services.ai.azure.com/api/projects/demo",
        "auth": {"type": "entra_id"},
        "apiProfiles": [{
            "apiType": "responses",
            "models": ["deployment", "deployment-secondary"],
            "defaultModel": "deployment",
            "versionMode": "v1",
            "options": {},
        }],
        "defaultSelection": {"apiType": "responses", "model": "deployment"},
    })


def project_write_payload() -> dict[str, Any]:
    return {
        "schemaVersion": 3,
        "endpointKind": "project",
        "endpoint": "https://example.services.ai.azure.com/api/projects/demo",
        "auth": {"type": "entra_id", "apiKey": {"action": "clear"}},
        "agentInstructions": "Test instructions",
        "apiProfiles": [{
            "apiType": "responses",
            "models": ["deployment", "deployment-secondary"],
            "defaultModel": "deployment",
            "versionMode": "v1",
            "options": {"store": False},
        }],
        "defaultSelection": {"apiType": "responses", "model": "deployment"},
    }


def test_foundry_settings_rest_contract_and_secret_redaction(monkeypatch):
    state: dict[str, FoundrySettings | None] = {"settings": None}

    def update(payload):
        state["settings"] = payload.resolve(state["settings"])
        return state["settings"]

    monkeypatch.setattr(backend, "load_foundry_settings", lambda: None)
    monkeypatch.setattr(backend, "update_foundry_settings", update)
    monkeypatch.setattr(backend.foundry_settings_store, "load", lambda: state["settings"])
    monkeypatch.setattr(backend, "foundry_settings", None)

    with TestClient(backend.fastapi_app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/foundry-settings/status").json() == {
            "isConfigured": False,
            "schemaVersion": 3,
        }
        assert client.get("/foundry-settings").status_code == 404

        response = client.put("/foundry-settings", json=project_write_payload())
        assert response.status_code == 200
        body = response.json()
        assert body["endpointKind"] == "project"
        assert body["apiProfiles"][0]["models"] == ["deployment", "deployment-secondary"]
        assert body["auth"] == {"type": "entra_id", "apiKeyConfigured": False}
        assert '"apiKey":' not in response.text
        assert client.get("/foundry-settings").json() == body
        assert client.get("/foundry-settings/status").json()["isConfigured"] is True


def test_foundry_settings_rest_rejects_project_api_key(monkeypatch):
    monkeypatch.setattr(backend, "load_foundry_settings", lambda: None)
    monkeypatch.setattr(backend, "update_foundry_settings", lambda payload: payload.resolve(None))
    monkeypatch.setattr(backend.foundry_settings_store, "load", lambda: None)
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


def test_request_validation_never_echoes_secret_input(monkeypatch):
    monkeypatch.setattr(backend, "load_foundry_settings", lambda: None)
    monkeypatch.setattr(backend, "foundry_settings", None)
    payload = project_write_payload()
    payload["unexpectedSecretField"] = "must-not-echo"

    with TestClient(backend.fastapi_app) as client:
        response = client.put("/foundry-settings", json=payload)

    assert response.status_code == 422
    assert "must-not-echo" not in response.text
    assert "unexpectedSecretField" in response.text


def test_secret_protection_failure_returns_recoverable_service_error(monkeypatch):
    monkeypatch.setattr(backend, "load_foundry_settings", lambda: None)
    monkeypatch.setattr(
        backend,
        "update_foundry_settings",
        lambda _payload: (_ for _ in ()).throw(SecretProtectionError("master key unavailable")),
    )
    monkeypatch.setattr(backend, "foundry_settings", None)
    monkeypatch.setattr(backend.foundry_settings_store, "load", lambda: None)

    with TestClient(backend.fastapi_app) as client:
        response = client.put("/foundry-settings", json=project_write_payload())

    assert response.status_code == 503
    assert response.json() == {"detail": "master key unavailable"}


def test_startup_decryption_failure_exposes_only_recoverable_non_secret_settings(monkeypatch):
    recoverable = FoundrySettings.model_validate({
        "schemaVersion": 3,
        "endpointKind": "model",
        "endpoint": "https://example.services.ai.azure.com",
        "auth": {"type": "api_key", "apiKey": "placeholder-only"},
        "apiProfiles": [{
            "apiType": "responses",
            "models": ["deployment", "deployment-secondary"],
            "defaultModel": "deployment",
            "versionMode": "v1",
            "options": {},
        }],
        "defaultSelection": {"apiType": "responses", "model": "deployment"},
    })
    monkeypatch.setattr(
        backend,
        "load_foundry_settings",
        lambda: (_ for _ in ()).throw(SecretProtectionError("original key is missing")),
    )
    monkeypatch.setattr(
        backend.foundry_settings_store,
        "load_recoverable_settings",
        lambda: recoverable,
    )

    with TestClient(backend.fastapi_app) as client:
        status = client.get("/foundry-settings/status").json()
        get_response = client.get("/foundry-settings")

    assert status["isConfigured"] is False
    assert status["error"] == "original key is missing"
    assert status["recoverableSettings"]["apiProfiles"][0]["models"] == [
        "deployment",
        "deployment-secondary",
    ]
    assert status["recoverableSettings"]["auth"]["apiKeyConfigured"] is False
    assert status["recoverableSettings"]["auth"]["apiKeyNeedsReplacement"] is True
    assert "apiKey" not in status["recoverableSettings"]["auth"]
    assert "placeholder-only" not in str(status)
    assert get_response.status_code == 503


def test_startup_corrupt_json_is_sanitized_and_does_not_prevent_health(monkeypatch):
    monkeypatch.setattr(
        backend,
        "load_foundry_settings",
        lambda: (_ for _ in ()).throw(json.JSONDecodeError("secret fragment", "x", 0)),
    )
    monkeypatch.setattr(
        backend.foundry_settings_store,
        "load_recoverable_settings",
        lambda: (_ for _ in ()).throw(json.JSONDecodeError("secret fragment", "x", 0)),
    )

    with TestClient(backend.fastapi_app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        status = client.get("/foundry-settings/status").json()

    assert status["isConfigured"] is False
    assert status["error"] == "Microsoft Foundry settings are invalid or corrupted. Replace the complete settings."
    assert "secret fragment" not in str(status)


def test_settings_update_rejects_active_model_removal(monkeypatch):
    current = project_settings()

    class BusyRuntime:
        async def apply_settings_update(self, _settings, _persist):
            raise AgentRunBusyError("active model cannot be removed")

        async def shutdown(self):
            return None

    monkeypatch.setattr(backend, "load_foundry_settings", lambda: current)
    busy_runtime = BusyRuntime()
    monkeypatch.setattr(backend, "AgentRuntime", lambda *_args, **_kwargs: busy_runtime)
    payload = project_write_payload()
    payload["apiProfiles"][0]["models"] = ["deployment"]

    with TestClient(backend.fastapi_app) as client:
        response = client.put("/foundry-settings", json=payload)

    assert response.status_code == 409
    assert response.json() == {"detail": "active model cannot be removed"}


@pytest.mark.asyncio
async def test_adapter_level_chat_error_has_complete_terminal_contract(monkeypatch):
    events: list[tuple[str, dict[str, Any], str | None]] = []

    async def capture(event: str, payload: dict[str, Any], room: str | None = None, **_kwargs: Any):
        events.append((event, payload, room))

    monkeypatch.setattr(backend.sio, "emit", capture)
    monkeypatch.setattr(backend, "foundry_settings", None)
    await backend.connect("socket-1", {})

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
        async def reserve_run(self, _session_id: str, _request_id: str):
            return object()

        async def release_run(self, _reservation):
            return None

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
    await backend.connect("socket-2", {})

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


@pytest.mark.asyncio
async def test_chat_send_resolves_the_complete_selected_model_configuration(monkeypatch):
    events: list[tuple[str, dict[str, Any], str | None]] = []
    session_id = f"backend-{uuid4()}"
    backend.session_manager.create(session_id, {"apiType": "responses", "model": "deployment"})

    async def capture(event: str, payload: dict[str, Any], room: str | None = None, **_kwargs: Any):
        events.append((event, payload, room))

    class RecordingRuntime:
        resolved_settings = None

        async def reserve_run(self, _session_id: str, _request_id: str):
            return object()

        async def release_run(self, _reservation):
            return None

        async def run(self, *, settings, **_kwargs: Any):
            self.resolved_settings = settings

    runtime = RecordingRuntime()
    monkeypatch.setattr(backend.sio, "emit", capture)
    monkeypatch.setattr(backend, "foundry_settings", project_settings())
    monkeypatch.setattr(backend, "agent_runtime", runtime)
    await backend.connect("socket-model", {})

    await backend.chat_send("socket-model", {
        "requestId": "request-model",
        "sessionId": session_id,
        "message": "hello",
        "selectedToolIds": [],
        "selectedModel": {"apiType": "responses", "model": "deployment-secondary"},
    })

    assert runtime.resolved_settings is not None
    assert runtime.resolved_settings.model == "deployment-secondary"
    assert runtime.resolved_settings.api_type.value == "responses"
    assert not [payload for event, payload, _ in events if event == "chat:error"]


@pytest.mark.asyncio
async def test_invalid_model_selection_does_not_create_orphan_session(monkeypatch):
    events: list[tuple[str, dict[str, Any], str | None]] = []
    before = set(backend.session_manager.sessions)

    async def capture(event: str, payload: dict[str, Any], room: str | None = None, **_kwargs: Any):
        events.append((event, payload, room))

    monkeypatch.setattr(backend.sio, "emit", capture)
    monkeypatch.setattr(backend, "foundry_settings", project_settings())
    monkeypatch.setattr(backend, "agent_runtime", object())
    await backend.connect("socket-invalid", {})

    await backend.chat_send("socket-invalid", {
        "requestId": "request-invalid",
        "sessionId": "does-not-exist",
        "message": "hello",
        "selectedToolIds": [],
        "selectedModel": {"apiType": "responses", "model": "missing"},
    })

    assert set(backend.session_manager.sessions) == before
    assert not [event for event, _, _ in events if event == "sessionCreated"]
    errors = [payload for event, payload, _ in events if event == "chat:error"]
    assert errors[-1]["code"] == "InvalidModelSelection"


@pytest.mark.asyncio
async def test_set_session_model_persists_valid_selection(monkeypatch):
    events: list[tuple[str, dict[str, Any], str | None]] = []
    session_id = f"backend-{uuid4()}"
    backend.session_manager.create(session_id, {"apiType": "responses", "model": "deployment"})

    async def capture(event: str, payload: dict[str, Any], room: str | None = None, **_kwargs: Any):
        events.append((event, payload, room))

    monkeypatch.setattr(backend.sio, "emit", capture)
    monkeypatch.setattr(backend, "foundry_settings", project_settings())
    monkeypatch.setattr(backend, "agent_runtime", None)

    await backend.setSessionModel("socket-select", {
        "sessionId": session_id,
        "selectedModel": {"apiType": "responses", "model": "deployment-secondary"},
    })

    assert backend.session_manager.get(session_id)["selectedModel"] == {
        "apiType": "responses",
        "model": "deployment-secondary",
    }
    updates = [payload for event, payload, _ in events if event == "sessionUpdated"]
    assert updates[-1]["selectedModel"]["model"] == "deployment-secondary"


@pytest.mark.asyncio
async def test_set_session_model_is_rejected_while_run_lock_is_active(monkeypatch):
    events: list[tuple[str, dict[str, Any], str | None]] = []
    session_id = f"backend-{uuid4()}"
    backend.session_manager.create(session_id, {"apiType": "responses", "model": "deployment"})

    async def capture(event: str, payload: dict[str, Any], room: str | None = None, **_kwargs: Any):
        events.append((event, payload, room))

    class BusyRuntime:
        async def set_selected_model(self, _session_id, _selection):
            raise AgentRunBusyError("busy")

    monkeypatch.setattr(backend.sio, "emit", capture)
    monkeypatch.setattr(backend, "foundry_settings", project_settings())
    monkeypatch.setattr(backend, "agent_runtime", BusyRuntime())

    await backend.setSessionModel("socket-busy", {
        "sessionId": session_id,
        "selectedModel": {"apiType": "responses", "model": "deployment-secondary"},
    })

    assert backend.session_manager.get(session_id)["selectedModel"]["model"] == "deployment"
    errors = [payload for event, payload, _ in events if event == "error"]
    assert errors[-1]["message"] == "The model cannot be changed while a run is active."
    rollbacks = [payload for event, payload, _ in events if event == "sessionUpdated"]
    assert rollbacks[-1]["selectedModel"]["model"] == "deployment"


@pytest.mark.asyncio
async def test_new_session_is_reserved_before_session_created_event_allows_settings_update(monkeypatch):
    events: list[tuple[str, dict[str, Any], str | None]] = []
    rejection_status: list[int] = []

    class ReservationRuntime:
        active = False
        resolved_settings = None

        async def reserve_run(self, _session_id: str, _request_id: str):
            self.active = True
            return object()

        async def release_run(self, _reservation):
            self.active = False

        async def run(self, *, settings, **_kwargs: Any):
            self.resolved_settings = settings
            self.active = False

        async def apply_settings_update(self, _settings, persist):
            if self.active:
                raise AgentRunBusyError("active model cannot be removed")
            return persist(), []

    runtime = ReservationRuntime()
    removal = project_write_payload()
    removal["apiProfiles"][0]["models"] = ["deployment"]
    removal["apiProfiles"][0]["defaultModel"] = "deployment"
    removal["defaultSelection"] = {"apiType": "responses", "model": "deployment"}

    async def capture(event: str, payload: dict[str, Any], room: str | None = None, **_kwargs: Any):
        events.append((event, payload, room))
        if event == "sessionCreated":
            try:
                await backend.set_foundry_settings(FoundrySettingsWrite.model_validate(removal))
            except HTTPException as exc:
                rejection_status.append(exc.status_code)

    monkeypatch.setattr(backend.sio, "emit", capture)
    monkeypatch.setattr(backend, "foundry_settings", project_settings())
    monkeypatch.setattr(backend, "agent_runtime", runtime)
    await backend.connect("socket-race", {})

    await backend.chat_send("socket-race", {
        "requestId": "request-race",
        "message": "hello",
        "selectedToolIds": [],
        "selectedModel": {"apiType": "responses", "model": "deployment-secondary"},
    })

    assert rejection_status == [409]
    assert runtime.resolved_settings.model == "deployment-secondary"
    created = [payload for event, payload, _ in events if event == "sessionCreated"]
    assert created[0]["selectedModel"]["model"] == "deployment-secondary"


@pytest.mark.asyncio
async def test_older_handler_cannot_unregister_newer_socket_run_ownership(monkeypatch):
    sid = f"socket-{uuid4()}"
    session_id = f"backend-{uuid4()}"
    backend.session_manager.create(session_id, {"apiType": "responses", "model": "deployment"})
    cancelled: list[tuple[str, str]] = []

    class OwnershipRuntime:
        async def reserve_run(self, _session_id: str, _request_id: str):
            return object()

        async def release_run(self, _reservation):
            return None

        async def run(self, **_kwargs: Any):
            return None

        async def cancel_and_wait(self, cancelled_session: str, cancelled_request: str):
            cancelled.append((cancelled_session, cancelled_request))
            return True

    async def capture(event: str, _payload: dict[str, Any], **_kwargs: Any):
        if event == "sessionUpdated":
            backend.socket_active_sessions[sid][session_id] = "newer-request"

    runtime = OwnershipRuntime()
    monkeypatch.setattr(backend.sio, "emit", capture)
    monkeypatch.setattr(backend, "foundry_settings", project_settings())
    monkeypatch.setattr(backend, "agent_runtime", runtime)
    await backend.connect(sid, {})

    await backend.chat_send(sid, {
        "requestId": "older-request",
        "sessionId": session_id,
        "message": "hello",
        "selectedToolIds": [],
    })

    assert backend.socket_active_sessions[sid][session_id] == "newer-request"
    await backend.disconnect(sid)
    assert cancelled == [(session_id, "newer-request")]


@pytest.mark.asyncio
async def test_disconnect_before_ownership_registration_aborts_new_session_and_run(monkeypatch):
    sid = f"socket-{uuid4()}"
    before = set(backend.session_manager.sessions)
    events: list[str] = []

    class DisconnectingRuntime:
        run_called = False
        released = False

        async def reserve_run(self, _session_id: str, _request_id: str):
            await backend.disconnect(sid)
            return object()

        async def release_run(self, _reservation):
            self.released = True

        async def run(self, **_kwargs: Any):
            self.run_called = True

        async def cancel_and_wait(self, _session_id: str, _request_id: str):
            return False

    async def capture(event: str, _payload: dict[str, Any], **_kwargs: Any):
        events.append(event)

    runtime = DisconnectingRuntime()
    monkeypatch.setattr(backend.sio, "emit", capture)
    monkeypatch.setattr(backend, "foundry_settings", project_settings())
    monkeypatch.setattr(backend, "agent_runtime", runtime)
    await backend.connect(sid, {})

    await backend.chat_send(sid, {
        "requestId": "disconnect-race",
        "message": "hello",
        "selectedToolIds": [],
        "selectedModel": {"apiType": "responses", "model": "deployment-secondary"},
    })

    assert runtime.released is True
    assert runtime.run_called is False
    assert set(backend.session_manager.sessions) == before
    assert "sessionCreated" not in events
    assert sid not in backend.socket_generations
    assert sid not in backend.socket_active_sessions
