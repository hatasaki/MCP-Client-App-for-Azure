from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.foundry_config import (
    FoundrySettings,
    FoundrySettingsStore,
    FoundrySettingsWrite,
    ModelSelection,
    migrate_legacy_config,
)
from app.secret_protection import SecretProtectionError, SecretProtector


@dataclass
class FixedKeyProvider:
    key: bytes

    def get_key(self, *, create: bool) -> bytes:
        return self.key


def protector(key: bytes = b"k" * 32) -> SecretProtector:
    return SecretProtector(FixedKeyProvider(key))


def responses_profile(
    models: list[str] | None = None,
    *,
    default: str | None = None,
    version_mode: str = "v1",
    api_version: str | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    deployments = models or ["responses-deployment"]
    return {
        "apiType": "responses",
        "models": deployments,
        "defaultModel": default or deployments[0],
        "versionMode": version_mode,
        **({"apiVersion": api_version} if api_version is not None else {}),
        "options": options or {},
    }


def chat_profile(
    models: list[str] | None = None,
    *,
    default: str | None = None,
    version_mode: str = "v1",
    api_version: str | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    deployments = models or ["chat-deployment"]
    return {
        "apiType": "chat_completions",
        "models": deployments,
        "defaultModel": default or deployments[0],
        "versionMode": version_mode,
        **({"apiVersion": api_version} if api_version is not None else {}),
        "options": options or {},
    }


def claude_profile(
    models: list[str] | None = None,
    *,
    default: str | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    deployments = models or ["claude-deployment"]
    return {
        "apiType": "claude_messages",
        "models": deployments,
        "defaultModel": default or deployments[0],
        "versionMode": "provider",
        "options": options if options is not None else {"maxTokens": 2048},
    }


def project_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schemaVersion": 3,
        "endpointKind": "project",
        "endpoint": "https://example.services.ai.azure.com/api/projects/demo/",
        "auth": {"type": "entra_id"},
        "agentInstructions": "Project instructions",
        "apiProfiles": [responses_profile(["gpt-primary", "gpt-secondary"], default="gpt-primary")],
        "defaultSelection": {"apiType": "responses", "model": "gpt-primary"},
    }
    payload.update(overrides)
    return payload


def model_payload(
    profiles: list[dict[str, Any]] | None = None,
    *,
    auth: dict[str, Any] | None = None,
    default_selection: dict[str, str] | None = None,
) -> dict[str, Any]:
    configured = profiles or [responses_profile()]
    first = configured[0]
    return {
        "schemaVersion": 3,
        "endpointKind": "model",
        "endpoint": "https://example.services.ai.azure.com",
        "auth": auth or {"type": "entra_id"},
        "agentInstructions": "Model instructions",
        "apiProfiles": configured,
        "defaultSelection": default_selection or {
            "apiType": first["apiType"],
            "model": first["defaultModel"],
        },
    }


def write_payload(
    profiles: list[dict[str, Any]] | None = None,
    *,
    auth_type: str = "entra_id",
    action: str = "clear",
    value: str | None = None,
) -> FoundrySettingsWrite:
    payload = model_payload(profiles)
    payload["auth"] = {
        "type": auth_type,
        "apiKey": {
            "action": action,
            **({"value": value} if value is not None else {}),
        },
    }
    return FoundrySettingsWrite.model_validate(payload)


def test_project_settings_are_normalized_multi_model_and_redacted():
    settings = FoundrySettings.model_validate(project_payload())

    assert settings.endpoint == "https://example.services.ai.azure.com/api/projects/demo"
    assert settings.available_selections()[1].model == "gpt-secondary"
    assert settings.public_dict()["auth"] == {
        "type": "entra_id",
        "apiKeyConfigured": False,
    }
    assert settings.public_dict()["schemaVersion"] == 3


def test_project_rejects_api_key_before_network_io():
    with pytest.raises(ValidationError, match="Project endpoints use Entra ID"):
        FoundrySettings.model_validate(project_payload(auth={"type": "api_key", "apiKey": "secret"}))


@pytest.mark.parametrize(
    "auth",
    [
        {"type": "entra_id"},
        {"type": "api_key", "apiKey": "secret"},
    ],
)
def test_model_endpoint_requires_https_for_every_authentication_mode(auth):
    with pytest.raises(ValidationError, match="require HTTPS"):
        payload = model_payload(auth=auth)
        payload["endpoint"] = "http://example.services.ai.azure.com"
        FoundrySettings.model_validate(payload)


@pytest.mark.parametrize(
    "profile",
    [
        chat_profile(),
        responses_profile(version_mode="dated", api_version="2025-04-01-preview"),
    ],
)
def test_project_rejects_unsupported_api_profiles(profile: dict[str, Any]):
    with pytest.raises(ValidationError):
        FoundrySettings.model_validate(project_payload(apiProfiles=[profile]))


def test_response_omit_preserves_false_zero_and_none_enum():
    profile = responses_profile(options={
        "temperature": 0,
        "reasoningEffort": "none",
        "store": False,
        "parallelToolCalls": False,
    })
    settings = FoundrySettings.model_validate(model_payload(
        [profile],
        auth={"type": "api_key", "apiKey": "secret"},
    ))

    assert settings.to_maf_options() == {
        "temperature": 0.0,
        "store": False,
        "allow_multiple_tool_calls": False,
        "reasoning": {"effort": "none"},
    }
    assert "top_p" not in settings.to_maf_options()


def test_chat_options_translate_to_maf_names():
    profile = chat_profile(
        version_mode="dated",
        api_version="2025-04-01-preview",
        options={"maxCompletionTokens": 123, "parallelToolCalls": True},
    )
    settings = FoundrySettings.model_validate(model_payload([profile]))

    assert settings.to_maf_options()["max_tokens"] == 123
    assert settings.to_maf_options()["allow_multiple_tool_calls"] is True


def test_claude_requires_max_tokens_and_maps_options():
    invalid = claude_profile(options={})
    with pytest.raises(ValidationError, match="maxTokens"):
        FoundrySettings.model_validate(model_payload([invalid]))

    profile = claude_profile(options={
        "maxTokens": 2048,
        "parallelToolUse": False,
        "effort": "high",
        "metadataUserId": "user-1",
    })
    settings = FoundrySettings.model_validate(model_payload([profile]))
    assert settings.to_maf_options() == {
        "max_tokens": 2048,
        "allow_multiple_tool_calls": False,
        "tool_choice": "auto",
        "output_config": {"effort": "high"},
        "metadata": {"user_id": "user-1"},
    }


def test_profiles_keep_api_specific_models_versions_and_options_isolated():
    profiles = [
        responses_profile(["shared", "reasoner"], default="reasoner", options={"temperature": 0}),
        chat_profile(
            ["shared", "chat-fast"],
            default="chat-fast",
            version_mode="dated",
            api_version="2025-04-01-preview",
            options={"temperature": 1.25, "maxCompletionTokens": 256},
        ),
        claude_profile(["shared"], options={"maxTokens": 512, "temperature": 0.4}),
    ]
    settings = FoundrySettings.model_validate(model_payload(
        profiles,
        default_selection={"apiType": "chat_completions", "model": "shared"},
    ))

    response = settings.resolve({"apiType": "responses", "model": "reasoner"})
    chat = settings.resolve({"apiType": "chat_completions", "model": "shared"})
    claude = settings.resolve({"apiType": "claude_messages", "model": "shared"})

    assert response.model == "reasoner"
    assert response.version_mode.value == "v1"
    assert response.to_maf_options()["temperature"] == 0
    assert chat.api_version == "2025-04-01-preview"
    assert chat.to_maf_options()["temperature"] == 1.25
    assert claude.to_maf_options()["max_tokens"] == 512
    assert [selection.model for selection in settings.available_selections()].count("shared") == 3


def test_duplicate_names_are_rejected_only_within_one_api_profile():
    with pytest.raises(ValidationError, match="unique within an API type"):
        FoundrySettings.model_validate(model_payload([responses_profile(["same", "same"])]))

    settings = FoundrySettings.model_validate(model_payload([
        responses_profile(["same"]),
        chat_profile(["same"]),
    ]))
    assert len(settings.available_selections()) == 2


def test_default_and_explicit_selections_must_reference_configured_models():
    with pytest.raises((ValidationError, KeyError)):
        FoundrySettings.model_validate(model_payload(
            [responses_profile(["one"])],
            default_selection={"apiType": "responses", "model": "missing"},
        ))

    settings = FoundrySettings.model_validate(model_payload([responses_profile(["one"])]))
    with pytest.raises(KeyError, match="missing"):
        settings.resolve({"apiType": "responses", "model": "missing"})
    assert settings.selection_exists({"apiType": "chat_completions", "model": "one"}) is False


def test_resolved_fingerprint_changes_with_model_and_never_exposes_secret():
    settings = FoundrySettings.model_validate(model_payload(
        [responses_profile(["one", "two"])],
        auth={"type": "api_key", "apiKey": "super-secret"},
    ))

    first = settings.resolve({"apiType": "responses", "model": "one"})
    second = settings.resolve({"apiType": "responses", "model": "two"})

    assert first.fingerprint() != second.fingerprint()
    assert "super-secret" not in json.dumps(settings.public_dict())
    assert settings.public_dict()["auth"]["apiKeyConfigured"] is True


def test_secret_update_keep_set_clear_uses_encrypted_storage(tmp_path: Path):
    path = tmp_path / "FoundrySettings.json"
    store = FoundrySettingsStore(path, protector=protector())

    first = store.update(write_payload(
        auth_type="api_key",
        action="set",
        value="first-secret",
    ))
    encrypted_text = path.read_text(encoding="utf-8")
    encrypted_payload = json.loads(encrypted_text)
    loaded = store.load()
    kept = store.update(write_payload(auth_type="api_key", action="keep"))
    cleared = store.update(write_payload(auth_type="entra_id", action="clear"))

    assert first.api_key == "first-secret"
    assert loaded is not None and loaded.api_key == "first-secret"
    assert kept.api_key == "first-secret"
    assert kept.credential_revision == first.credential_revision
    assert cleared.api_key is None
    assert cleared.credential_revision == first.credential_revision + 1
    assert "first-secret" not in encrypted_text
    assert "apiKey" not in encrypted_payload["auth"]
    assert encrypted_payload["auth"]["apiKeyEncrypted"]["algorithm"] == "AES-256-GCM"
    persisted_after_clear = json.loads(path.read_text(encoding="utf-8"))
    assert persisted_after_clear["auth"] == {"type": "entra_id"}


@pytest.mark.parametrize("mutation", ["endpoint", "profile", "instructions", "revision"])
def test_encrypted_key_is_bound_to_all_non_secret_settings(tmp_path: Path, mutation: str):
    path = tmp_path / "FoundrySettings.json"
    store = FoundrySettingsStore(path, protector=protector())
    store.update(write_payload(auth_type="api_key", action="set", value="bound-secret"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    if mutation == "endpoint":
        payload["endpoint"] = "https://attacker.example"
    elif mutation == "profile":
        payload["apiProfiles"][0]["models"] = ["substituted-model"]
        payload["apiProfiles"][0]["defaultModel"] = "substituted-model"
        payload["defaultSelection"]["model"] = "substituted-model"
    elif mutation == "instructions":
        payload["agentInstructions"] = "tampered"
    else:
        payload["credentialRevision"] += 1
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SecretProtectionError, match="invalid, corrupted"):
        store.load()


def test_v2_plaintext_config_is_atomically_rewritten_as_encrypted_v3(tmp_path: Path):
    target = tmp_path / "FoundrySettings.json"
    legacy = tmp_path / "AzureOpenAI.json"
    backup = legacy.with_suffix(legacy.suffix + ".pre-foundry.bak")
    target.write_text(json.dumps({
        "schemaVersion": 2,
        "endpointKind": "model",
        "endpoint": "https://example.services.ai.azure.com",
        "model": "v2-deployment",
        "apiType": "responses",
        "versionMode": "v1",
        "auth": {"type": "api_key", "apiKey": "v2-plaintext-key"},
        "agentInstructions": "v2 instructions",
        "options": {"store": False},
        "credentialRevision": 4,
    }), encoding="utf-8")
    legacy.write_text('{"api_key":"stale-legacy-key"}', encoding="utf-8")
    backup.write_text('{"api_key":"stale-backup-key"}', encoding="utf-8")

    store = FoundrySettingsStore(target, legacy, protector())
    migrated = store.load()
    persisted_text = target.read_text(encoding="utf-8")
    persisted = json.loads(persisted_text)

    assert migrated is not None and migrated.api_key == "v2-plaintext-key"
    assert migrated.default_selection.model == "v2-deployment"
    assert persisted["schemaVersion"] == 3
    assert persisted["auth"]["apiKeyEncrypted"]["algorithm"] == "AES-256-GCM"
    assert "v2-plaintext-key" not in persisted_text
    assert not legacy.exists()
    assert not backup.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_legacy_azure_config_migrates_encrypted_without_plaintext_backup(tmp_path: Path):
    legacy = tmp_path / "AzureOpenAI.json"
    target = tmp_path / "FoundrySettings.json"
    backup = legacy.with_suffix(legacy.suffix + ".pre-foundry.bak")
    legacy.write_text(json.dumps({
        "endpoint": "https://example.openai.azure.com/",
        "api_key": "legacy-key",
        "deployment": "deployment",
        "api_version": "2025-04-01-preview",
        "api_type": "responses",
        "system_prompt": "Legacy instructions",
        "temperature": 0,
        "reasoning_effort": "none",
    }), encoding="utf-8")
    backup.write_text('{"api_key":"older-key"}', encoding="utf-8")

    store = FoundrySettingsStore(target, legacy, protector())
    migrated = store.load()
    loaded_again = store.load()
    persisted_text = target.read_text(encoding="utf-8")

    assert migrated is not None and migrated.api_key == "legacy-key"
    assert migrated.agent_instructions == "Legacy instructions"
    assert migrated.to_maf_options()["reasoning"] == {"effort": "none"}
    assert loaded_again is not None and loaded_again.api_key == "legacy-key"
    assert "legacy-key" not in persisted_text
    assert "apiKeyEncrypted" in persisted_text
    assert not legacy.exists()
    assert not backup.exists()


def test_legacy_plaintext_deletion_failure_is_not_silently_ignored(tmp_path: Path, monkeypatch):
    legacy = tmp_path / "AzureOpenAI.json"
    target = tmp_path / "FoundrySettings.json"
    legacy.write_text(json.dumps({
        "endpoint": "https://example.openai.azure.com/",
        "api_key": "legacy-key",
        "deployment": "deployment",
        "api_type": "responses",
    }), encoding="utf-8")
    original_unlink = Path.unlink

    def blocked_unlink(path: Path, *args: Any, **kwargs: Any):
        if path == legacy:
            raise PermissionError("blocked")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", blocked_unlink)
    store = FoundrySettingsStore(target, legacy, protector())

    with pytest.raises(SecretProtectionError, match="could not be deleted"):
        store.load()

    assert target.exists()
    assert "legacy-key" not in target.read_text(encoding="utf-8")
    assert legacy.exists()


def test_schema_v3_rejects_plaintext_api_key_field(tmp_path: Path):
    path = tmp_path / "FoundrySettings.json"
    payload = model_payload(auth={"type": "api_key", "apiKey": "plaintext"})
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="must not contain a plaintext"):
        FoundrySettingsStore(path, protector=protector()).load()


def test_corrupt_storage_requires_explicit_full_replacement(tmp_path: Path):
    path = tmp_path / "FoundrySettings.json"
    path.write_text('{"schemaVersion":3,"auth":', encoding="utf-8")
    store = FoundrySettingsStore(path, protector=protector())

    with pytest.raises(SecretProtectionError, match="invalid or corrupted"):
        store.update(write_payload(auth_type="api_key", action="keep"))

    replacement = store.update(
        write_payload(auth_type="api_key", action="set", value="new-secret")
    )
    assert replacement.api_key == "new-secret"
    assert store.load().api_key == "new-secret"
    assert "new-secret" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("content", ["[]", "null", '"string"'])
def test_non_object_storage_requires_explicit_full_replacement(tmp_path: Path, content: str):
    path = tmp_path / "FoundrySettings.json"
    path.write_text(content, encoding="utf-8")
    store = FoundrySettingsStore(path, protector=protector())

    with pytest.raises(SecretProtectionError, match="invalid or corrupted"):
        store.update(write_payload(auth_type="api_key", action="keep"))
    replacement = store.update(write_payload(auth_type="entra_id", action="clear"))
    assert replacement.auth.type.value == "entra_id"


def test_unreadable_key_can_only_be_replaced_explicitly(tmp_path: Path):
    path = tmp_path / "FoundrySettings.json"
    original = FoundrySettingsStore(path, protector=protector(b"a" * 32)).update(
        write_payload(auth_type="api_key", action="set", value="old-secret")
    )
    wrong_key_store = FoundrySettingsStore(path, protector=protector(b"b" * 32))

    with pytest.raises(SecretProtectionError, match="does not match"):
        wrong_key_store.update(write_payload(auth_type="api_key", action="keep"))

    replacement_payload = write_payload(auth_type="api_key", action="set", value="replacement-secret")
    replacement_payload.api_profiles = [
        FoundrySettings.model_validate(model_payload([
            responses_profile(["retained-primary", "retained-secondary"], options={"store": False})
        ])).api_profiles[0]
    ]
    replacement_payload.default_selection = ModelSelection(
        api_type="responses",
        model="retained-primary",
    )
    replaced = wrong_key_store.update(replacement_payload)
    assert replaced.api_key == "replacement-secret"
    assert replaced.credential_revision == 2
    assert replaced.fingerprint() != original.fingerprint()
    assert replaced.get_profile("responses").models == ["retained-primary", "retained-secondary"]
    assert wrong_key_store.load().api_key == "replacement-secret"
    assert "replacement-secret" not in path.read_text(encoding="utf-8")


def test_v2_shape_is_accepted_for_in_memory_compatibility():
    settings = FoundrySettings.model_validate({
        "schemaVersion": 2,
        "endpointKind": "model",
        "endpoint": "https://example.services.ai.azure.com",
        "model": "legacy-shape",
        "apiType": "responses",
        "versionMode": "v1",
        "auth": {"type": "entra_id"},
        "options": {},
    })

    assert settings.schema_version == 3
    assert settings.default_selection.model == "legacy-shape"


def test_migrate_blank_legacy_config_returns_none():
    assert migrate_legacy_config({"endpoint": "", "deployment": ""}) is None
