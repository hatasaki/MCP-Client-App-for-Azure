from __future__ import annotations

import json
from pathlib import Path

from agent_framework import AgentSession

from app.foundry_config import FoundrySettings
from app.session_manager import SessionManager


def multi_model_settings() -> FoundrySettings:
    return FoundrySettings.model_validate({
        "schemaVersion": 4,
        "endpointKind": "model",
        "endpoint": "https://example.services.ai.azure.com",
        "auth": {"type": "entra_id"},
        "apiProfiles": [{
            "apiType": "responses",
            "models": ["primary", "secondary"],
            "versionMode": "v1",
            "options": {},
        }],
        "defaultSelection": {"apiType": "responses", "model": "primary"},
    })


def test_legacy_session_migrates_without_response_id(tmp_path: Path):
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps({
        "id": "legacy",
        "name": "Chat old",
        "messages": [
            {"role": "user", "content": "hello", "timestamp": "2025-01-01T00:00:00Z"},
            {"role": "assistant", "content": "hi", "timestamp": "2025-01-01T00:00:01Z"},
        ],
        "createdAt": "2025-01-01T00:00:00Z",
        "updatedAt": "2025-01-01T00:00:01Z",
        "responseId": "resp_old",
    }), encoding="utf-8")

    manager = SessionManager(tmp_path)
    session = manager.get_session("legacy")

    assert session is not None
    assert session["schemaVersion"] == 4
    assert session["selectedModel"] is None
    assert "responseId" not in session
    assert all(message["status"] == "completed" for message in session["messages"])
    assert all(message["id"] for message in session["messages"])
    assert all(message["attachments"] == [] for message in session["messages"])


def test_matching_fingerprint_restores_full_agent_session(tmp_path: Path):
    manager = SessionManager(tmp_path)
    manager.create("chat")
    original = AgentSession(session_id="chat", service_session_id="resp_123")
    original.state["custom"] = {"value": 42}
    manager.save_agent_session("chat", original, "same")

    restored, replay_required, reset = manager.prepare_agent_session("chat", "same")

    assert restored.service_session_id == "resp_123"
    assert restored.state == {"custom": {"value": 42}}
    assert replay_required is False
    assert reset is False


def test_first_run_of_empty_session_is_not_reported_as_state_reset(tmp_path: Path):
    manager = SessionManager(tmp_path)
    manager.create("chat")

    fresh, replay_required, reset = manager.prepare_agent_session("chat", "first")

    assert fresh.service_session_id is None
    assert replay_required is False
    assert reset is False
    assert manager.get_session("chat")["stateEpoch"] == 0


def test_changed_fingerprint_replays_only_completed_text(tmp_path: Path):
    manager = SessionManager(tmp_path)
    manager.create("chat")
    manager.append_message("chat", role="user", content="first")
    manager.append_message("chat", role="assistant", content="answer")
    manager.append_message("chat", role="assistant", content="partial", status="cancelled")
    original = AgentSession(session_id="chat", service_session_id="resp_old")
    manager.save_agent_session("chat", original, "old")

    fresh, replay_required, reset = manager.prepare_agent_session("chat", "new")
    replay = manager.build_replay_messages(manager.get_session("chat"))

    assert fresh.service_session_id is None
    assert [message.role for message in replay] == ["user", "assistant"]
    assert [message.text for message in replay] == ["first", "answer"]
    assert replay_required is True
    assert reset is True
    assert manager.get_session("chat")["stateEpoch"] == 1
    assert manager.get_session("chat")["mafState"] is None

    # A process restart before the new run is saved must not revive old MAF state.
    reloaded = SessionManager(tmp_path)
    restarted, restarted_replay_required, restarted_reset = reloaded.prepare_agent_session("chat", "new")
    restarted_replay = reloaded.build_replay_messages(reloaded.get_session("chat"))
    assert restarted.service_session_id is None
    assert [message.text for message in restarted_replay] == ["first", "answer"]
    assert restarted_replay_required is True
    assert restarted_reset is True


def test_cancel_rollback_restores_pre_run_state(tmp_path: Path):
    manager = SessionManager(tmp_path)
    manager.create("chat")
    original = AgentSession(session_id="chat", service_session_id="resp_before")
    manager.save_agent_session("chat", original, "fp")
    running, _, _ = manager.prepare_agent_session("chat", "fp")
    running.service_session_id = "resp_after"
    manager.save_agent_session("chat", running, "fp")

    # Simulate a new run whose state is snapshotted and then cancelled.
    manager.prepare_agent_session("chat", "fp")
    manager.rollback_agent_session("chat")

    restored = AgentSession.from_dict(manager.get_session("chat")["mafState"])
    assert restored.service_session_id == "resp_after"


def test_replay_tail_is_deterministic(tmp_path: Path):
    manager = SessionManager(tmp_path, max_replay_characters=5)
    manager.create("chat")
    manager.append_message("chat", role="user", content="1111")
    manager.append_message("chat", role="assistant", content="2222")
    manager.append_message("chat", role="user", content="3333")

    replay = manager.build_replay_messages(manager.get_session("chat"))

    assert [message.text for message in replay] == ["3333"]


def test_public_session_never_exposes_maf_state_or_fingerprint(tmp_path: Path):
    manager = SessionManager(tmp_path)
    manager.create("chat")
    agent_session = AgentSession(session_id="chat")
    agent_session.state["secret-shaped-provider-state"] = "opaque"
    manager.save_agent_session("chat", agent_session, "fingerprint")

    public = manager.get("chat")

    assert "mafState" not in public
    assert "preRunMafState" not in public
    assert "configFingerprint" not in public


def test_selected_model_is_persisted_and_reloaded(tmp_path: Path):
    manager = SessionManager(tmp_path)
    created = manager.create(
        "chat",
        {"apiType": "responses", "model": "primary"},
    )
    updated = manager.set_selected_model(
        "chat",
        {"apiType": "responses", "model": "secondary"},
    )
    reloaded = SessionManager(tmp_path)

    assert created["selectedModel"]["model"] == "primary"
    assert updated["selectedModel"]["model"] == "secondary"
    assert reloaded.selected_model("chat").model == "secondary"


def test_reconcile_resets_only_missing_or_invalid_model_selections(tmp_path: Path):
    manager = SessionManager(tmp_path)
    manager.create("valid", {"apiType": "responses", "model": "secondary"})
    manager.create("removed", {"apiType": "responses", "model": "removed"})
    manager.create("legacy")
    timestamps = {
        session_id: manager.get(session_id)["updatedAt"]
        for session_id in ("valid", "removed", "legacy")
    }

    updated = manager.reconcile_model_selections(multi_model_settings())

    assert {session["id"] for session in updated} == {"removed", "legacy"}
    assert manager.get("valid")["selectedModel"]["model"] == "secondary"
    assert manager.get("removed")["selectedModel"]["model"] == "primary"
    assert manager.get("legacy")["selectedModel"]["model"] == "primary"
    assert {
        session_id: manager.get(session_id)["updatedAt"]
        for session_id in ("valid", "removed", "legacy")
    } == timestamps
