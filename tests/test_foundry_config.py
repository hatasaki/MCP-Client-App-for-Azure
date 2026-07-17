from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.foundry_config import (
    FoundrySettings,
    FoundrySettingsStore,
    FoundrySettingsWrite,
    migrate_legacy_config,
)


def project_settings(**overrides):
    payload = {
        "endpointKind": "project",
        "endpoint": "https://example.services.ai.azure.com/api/projects/demo/",
        "model": "gpt-5.6-sol",
        "apiType": "responses",
        "versionMode": "v1",
        "auth": {"type": "entra_id"},
        "options": {},
    }
    payload.update(overrides)
    return FoundrySettings.model_validate(payload)


def test_project_settings_are_normalized_and_redacted():
    settings = project_settings()

    assert settings.endpoint == "https://example.services.ai.azure.com/api/projects/demo"
    assert settings.public_dict()["auth"] == {
        "type": "entra_id",
        "apiKeyConfigured": False,
    }
    assert settings.public_dict()["endpointKind"] == "project"


def test_project_rejects_api_key_before_network_io():
    with pytest.raises(ValidationError, match="Project endpoints use Entra ID"):
        project_settings(auth={"type": "api_key", "apiKey": "secret"})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("apiType", "chat_completions"),
        ("versionMode", "dated"),
        ("apiVersion", "2025-04-01-preview"),
    ],
)
def test_project_rejects_unsupported_matrix(field, value):
    with pytest.raises(ValidationError):
        project_settings(**{field: value})


def test_response_omit_preserves_false_zero_and_none_enum():
    settings = FoundrySettings.model_validate({
        "endpointKind": "model",
        "endpoint": "https://example.openai.azure.com/openai/v1/",
        "model": "deployment",
        "apiType": "responses",
        "versionMode": "v1",
        "auth": {"type": "api_key", "apiKey": "secret"},
        "options": {
            "temperature": 0,
            "reasoningEffort": "none",
            "store": False,
            "parallelToolCalls": False,
        },
    })

    assert settings.endpoint == "https://example.openai.azure.com"
    assert settings.to_maf_options() == {
        "temperature": 0.0,
        "store": False,
        "allow_multiple_tool_calls": False,
        "reasoning": {"effort": "none"},
    }
    assert "top_p" not in settings.to_maf_options()


def test_chat_options_translate_to_maf_names():
    settings = FoundrySettings.model_validate({
        "endpointKind": "model",
        "endpoint": "https://example.services.ai.azure.com",
        "model": "deployment",
        "apiType": "chat_completions",
        "versionMode": "dated",
        "apiVersion": "2025-04-01-preview",
        "auth": {"type": "entra_id"},
        "options": {"maxCompletionTokens": 123, "parallelToolCalls": True},
    })

    assert settings.to_maf_options()["max_tokens"] == 123
    assert settings.to_maf_options()["allow_multiple_tool_calls"] is True


def test_claude_requires_max_tokens_and_maps_options():
    base = {
        "endpointKind": "model",
        "endpoint": "https://example.services.ai.azure.com/anthropic",
        "model": "claude-deployment",
        "apiType": "claude_messages",
        "versionMode": "provider",
        "auth": {"type": "api_key", "apiKey": "secret"},
    }
    with pytest.raises(ValidationError, match="maxTokens"):
        FoundrySettings.model_validate({**base, "options": {}})

    settings = FoundrySettings.model_validate({
        **base,
        "options": {
            "maxTokens": 2048,
            "parallelToolUse": False,
            "effort": "high",
            "metadataUserId": "user-1",
        },
    })
    assert settings.to_maf_options() == {
        "max_tokens": 2048,
        "allow_multiple_tool_calls": False,
        "tool_choice": "auto",
        "output_config": {"effort": "high"},
        "metadata": {"user_id": "user-1"},
    }


def test_secret_update_keep_set_clear(tmp_path: Path):
    store = FoundrySettingsStore(tmp_path / "FoundrySettings.json")
    common = {
        "endpointKind": "model",
        "endpoint": "https://example.openai.azure.com",
        "model": "deployment",
        "apiType": "responses",
        "versionMode": "v1",
        "options": {},
    }

    first = store.update(FoundrySettingsWrite.model_validate({
        **common,
        "auth": {"type": "api_key", "apiKey": {"action": "set", "value": "first-secret"}},
    }))
    kept = store.update(FoundrySettingsWrite.model_validate({
        **common,
        "auth": {"type": "api_key", "apiKey": {"action": "keep"}},
    }))
    cleared = store.update(FoundrySettingsWrite.model_validate({
        **common,
        "auth": {"type": "entra_id", "apiKey": {"action": "clear"}},
    }))

    assert first.api_key == "first-secret"
    assert kept.api_key == "first-secret"
    assert kept.credential_revision == first.credential_revision
    assert cleared.api_key is None
    assert cleared.credential_revision == first.credential_revision + 1
    persisted = json.loads((tmp_path / "FoundrySettings.json").read_text(encoding="utf-8"))
    assert "apiKey" not in persisted["auth"]


def test_legacy_config_migrates_once_with_backup(tmp_path: Path):
    legacy_path = tmp_path / "AzureOpenAI.json"
    target_path = tmp_path / "FoundrySettings.json"
    legacy_path.write_text(json.dumps({
        "endpoint": "https://example.openai.azure.com/",
        "api_key": "legacy-key",
        "deployment": "deployment",
        "api_version": "2025-04-01-preview",
        "api_type": "responses",
        "system_prompt": "Legacy instructions",
        "temperature": 0,
        "reasoning_effort": "none",
    }), encoding="utf-8")

    store = FoundrySettingsStore(target_path, legacy_path)
    migrated = store.load()
    loaded_again = store.load()

    assert migrated is not None
    assert migrated.api_key == "legacy-key"
    assert migrated.agent_instructions == "Legacy instructions"
    assert migrated.to_maf_options()["reasoning"] == {"effort": "none"}
    assert loaded_again is not None
    assert target_path.exists()
    assert legacy_path.with_suffix(".json.pre-foundry.bak").exists()


def test_fingerprint_does_not_contain_secret():
    settings = FoundrySettings.model_validate({
        "endpointKind": "model",
        "endpoint": "https://example.openai.azure.com",
        "model": "deployment",
        "apiType": "responses",
        "versionMode": "v1",
        "auth": {"type": "api_key", "apiKey": "super-secret"},
        "options": {},
    })
    assert "super-secret" not in settings.fingerprint()
    assert "super-secret" not in json.dumps(settings.public_dict())


def test_migrate_blank_legacy_config_returns_none():
    assert migrate_legacy_config({"endpoint": "", "deployment": ""}) is None
