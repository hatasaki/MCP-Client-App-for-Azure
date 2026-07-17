from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Literal, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    TypeAdapter,
    field_validator,
    model_validator,
)

SCHEMA_VERSION = 2

DEFAULT_AGENT_INSTRUCTIONS = (
    "Based on the user's instructions, analyze the user's intent, define goals to achieve that intent, "
    "invoke and execute necessary tools until the goals are accomplished, and finally return the response to the user."
)


def _to_camel(name: str) -> str:
    first, *rest = name.split("_")
    return first + "".join(part.capitalize() for part in rest)


class StrictModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class EndpointKind(str, Enum):
    PROJECT = "project"
    MODEL = "model"


class ApiType(str, Enum):
    RESPONSES = "responses"
    CHAT_COMPLETIONS = "chat_completions"
    CLAUDE_MESSAGES = "claude_messages"


class VersionMode(str, Enum):
    V1 = "v1"
    DATED = "dated"
    PROVIDER = "provider"


class AuthType(str, Enum):
    ENTRA_ID = "entra_id"
    API_KEY = "api_key"


class SecretAction(str, Enum):
    KEEP = "keep"
    SET = "set"
    CLEAR = "clear"


ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh"]
Verbosity = Literal["low", "medium", "high"]


class AuthConfig(StrictModel):
    type: AuthType = AuthType.ENTRA_ID
    api_key: SecretStr | None = None

    @model_validator(mode="after")
    def validate_key(self) -> "AuthConfig":
        if self.type == AuthType.API_KEY and not self.api_key:
            raise ValueError("An API key is required when API key authentication is selected.")
        if self.type == AuthType.ENTRA_ID:
            object.__setattr__(self, "api_key", None)
        return self


class ApiKeyUpdate(StrictModel):
    action: SecretAction = SecretAction.KEEP
    value: str | None = None

    @model_validator(mode="after")
    def validate_action(self) -> "ApiKeyUpdate":
        if self.action == SecretAction.SET and not (self.value or "").strip():
            raise ValueError("A non-empty API key is required when action is 'set'.")
        if self.action != SecretAction.SET:
            object.__setattr__(self, "value", None)
        return self


class AuthUpdate(StrictModel):
    type: AuthType = AuthType.ENTRA_ID
    api_key: ApiKeyUpdate = Field(default_factory=ApiKeyUpdate)


class ResponsesOptions(StrictModel):
    temperature: Annotated[float | None, Field(ge=0, le=2)] = None
    top_p: Annotated[float | None, Field(ge=0, le=1)] = None
    max_output_tokens: Annotated[int | None, Field(gt=0)] = None
    reasoning_effort: ReasoningEffort | None = None
    reasoning_summary: Literal["auto", "concise", "detailed"] | None = None
    verbosity: Verbosity | None = None
    store: bool | None = None
    parallel_tool_calls: bool | None = None
    service_tier: Literal["auto", "default", "flex", "priority"] | None = None
    truncation: Literal["auto", "disabled"] | None = None
    max_tool_calls: Annotated[int | None, Field(gt=0)] = None
    safety_identifier: Annotated[str | None, Field(max_length=64)] = None
    prompt_cache_key: str | None = None
    metadata: dict[str, str] | None = None

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        return _validate_openai_metadata(value)

    def to_maf_options(self) -> dict[str, Any]:
        values = self.model_dump(exclude_none=True)
        options: dict[str, Any] = {}
        for key, value in values.items():
            if key == "max_output_tokens":
                options["max_tokens"] = value
            elif key == "parallel_tool_calls":
                options["allow_multiple_tool_calls"] = value
            elif key in {"reasoning_effort", "reasoning_summary"}:
                continue
            else:
                options[key] = value
        reasoning: dict[str, Any] = {}
        if self.reasoning_effort is not None:
            reasoning["effort"] = self.reasoning_effort
        if self.reasoning_summary is not None:
            reasoning["summary"] = self.reasoning_summary
        if reasoning:
            options["reasoning"] = reasoning
        return options


class ChatCompletionsOptions(StrictModel):
    temperature: Annotated[float | None, Field(ge=0, le=2)] = None
    top_p: Annotated[float | None, Field(ge=0, le=1)] = None
    max_completion_tokens: Annotated[int | None, Field(gt=0)] = None
    reasoning_effort: ReasoningEffort | None = None
    verbosity: Verbosity | None = None
    stop: str | list[str] | None = None
    seed: int | None = None
    frequency_penalty: Annotated[float | None, Field(ge=-2, le=2)] = None
    presence_penalty: Annotated[float | None, Field(ge=-2, le=2)] = None
    logprobs: bool | None = None
    top_logprobs: Annotated[int | None, Field(ge=0, le=20)] = None
    store: bool | None = None
    parallel_tool_calls: bool | None = None
    service_tier: Literal["auto", "default", "flex", "priority"] | None = None
    safety_identifier: Annotated[str | None, Field(max_length=64)] = None
    prompt_cache_key: str | None = None
    metadata: dict[str, str] | None = None

    @field_validator("stop")
    @classmethod
    def validate_stop(cls, value: str | list[str] | None) -> str | list[str] | None:
        if isinstance(value, list):
            if not value:
                raise ValueError("stop must contain at least one sequence when provided as a list.")
            if any(not item for item in value):
                raise ValueError("stop sequences must not be empty.")
        elif value == "":
            raise ValueError("stop must not be empty.")
        return value

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        return _validate_openai_metadata(value)

    def to_maf_options(self) -> dict[str, Any]:
        values = self.model_dump(exclude_none=True)
        options: dict[str, Any] = {}
        for key, value in values.items():
            if key == "max_completion_tokens":
                options["max_tokens"] = value
            elif key == "parallel_tool_calls":
                options["allow_multiple_tool_calls"] = value
            else:
                options[key] = value
        return options


class ClaudeThinkingDisabled(StrictModel):
    type: Literal["disabled"] = "disabled"


class ClaudeThinkingEnabled(StrictModel):
    type: Literal["enabled"] = "enabled"
    budget_tokens: Annotated[int, Field(gt=0)]


class ClaudeThinkingAdaptive(StrictModel):
    type: Literal["adaptive"] = "adaptive"


ClaudeThinking = Annotated[
    ClaudeThinkingDisabled | ClaudeThinkingEnabled | ClaudeThinkingAdaptive,
    Field(discriminator="type"),
]


class ClaudeMessagesOptions(StrictModel):
    max_tokens: Annotated[int, Field(gt=0)]
    temperature: Annotated[float | None, Field(ge=0, le=1)] = None
    top_p: Annotated[float | None, Field(ge=0, le=1)] = None
    top_k: Annotated[int | None, Field(gt=0)] = None
    stop_sequences: list[str] | None = None
    thinking: ClaudeThinking | None = None
    effort: Literal["low", "medium", "high", "max"] | None = None
    service_tier: Literal["auto", "standard_only"] | None = None
    parallel_tool_use: bool | None = None
    metadata_user_id: str | None = None

    @field_validator("stop_sequences")
    @classmethod
    def validate_stop_sequences(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and (not value or any(not item for item in value)):
            raise ValueError("stopSequences must contain non-empty strings.")
        return value

    def to_maf_options(self) -> dict[str, Any]:
        options: dict[str, Any] = {"max_tokens": self.max_tokens}
        values = self.model_dump(exclude_none=True)
        for key, value in values.items():
            if key == "stop_sequences":
                options["stop"] = value
            elif key == "effort":
                options["output_config"] = {"effort": value}
            elif key == "parallel_tool_use":
                options["allow_multiple_tool_calls"] = value
                # MAF emits Anthropic's disable_parallel_tool_use only when a
                # tool choice exists. Explicit auto preserves default selection
                # while keeping false distinct from an omitted setting.
                options.setdefault("tool_choice", "auto")
            elif key == "metadata_user_id":
                options["metadata"] = {"user_id": value}
            elif key != "max_tokens":
                options[key] = value
        return options


OptionsType = ResponsesOptions | ChatCompletionsOptions | ClaudeMessagesOptions


class FoundrySettings(StrictModel):
    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    endpoint_kind: EndpointKind
    endpoint: str
    model: str
    api_type: ApiType
    version_mode: VersionMode
    api_version: str | None = None
    auth: AuthConfig = Field(default_factory=AuthConfig)
    agent_instructions: str = DEFAULT_AGENT_INSTRUCTIONS
    options: OptionsType
    credential_revision: Annotated[int, Field(ge=0)] = 0

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        if not value:
            raise ValueError("Endpoint is required.")
        return value

    @field_validator("model")
    @classmethod
    def validate_model_name(cls, value: str) -> str:
        if not value:
            raise ValueError("Model deployment name is required.")
        return value

    @model_validator(mode="before")
    @classmethod
    def parse_options_for_api(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        mutable = dict(data)
        api_value = mutable.get("api_type", mutable.get("apiType"))
        options = mutable.get("options") or {}
        if isinstance(api_value, ApiType):
            api_value = api_value.value
        option_models: dict[str, type[StrictModel]] = {
            ApiType.RESPONSES.value: ResponsesOptions,
            ApiType.CHAT_COMPLETIONS.value: ChatCompletionsOptions,
            ApiType.CLAUDE_MESSAGES.value: ClaudeMessagesOptions,
        }
        option_model = option_models.get(str(api_value))
        if option_model and not isinstance(options, option_model):
            mutable["options"] = option_model.model_validate(options)
        return mutable

    @model_validator(mode="after")
    def validate_matrix(self) -> "FoundrySettings":
        if self.endpoint_kind == EndpointKind.PROJECT:
            object.__setattr__(self, "endpoint", normalize_project_endpoint(self.endpoint))
            if self.api_type != ApiType.RESPONSES:
                raise ValueError("Project endpoints support the Responses API only.")
            if self.version_mode != VersionMode.V1:
                raise ValueError("Project endpoints use the v1 API and do not accept a dated API version.")
            if self.auth.type != AuthType.ENTRA_ID:
                raise ValueError(
                    "Project endpoints use Entra ID with FoundryChatClient. Select a Model endpoint to use an API key."
                )
            if self.api_version is not None:
                raise ValueError("apiVersion must be omitted for Project endpoints.")
        else:
            object.__setattr__(self, "endpoint", normalize_model_endpoint(self.endpoint))
            if self.api_type == ApiType.CLAUDE_MESSAGES:
                if self.version_mode != VersionMode.PROVIDER:
                    raise ValueError("Claude Messages uses its provider API version mode.")
                if self.api_version is not None:
                    raise ValueError("apiVersion must be omitted for Claude Messages.")
            elif self.version_mode == VersionMode.PROVIDER:
                raise ValueError("Provider version mode is valid only for Claude Messages.")
            elif self.version_mode == VersionMode.DATED:
                if not self.api_version or not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:-preview)?", self.api_version):
                    raise ValueError("A dated API version such as 2025-04-01-preview is required.")
            elif self.api_version is not None:
                raise ValueError("apiVersion must be omitted when versionMode is v1.")
        expected_options: dict[ApiType, type[StrictModel]] = {
            ApiType.RESPONSES: ResponsesOptions,
            ApiType.CHAT_COMPLETIONS: ChatCompletionsOptions,
            ApiType.CLAUDE_MESSAGES: ClaudeMessagesOptions,
        }
        if not isinstance(self.options, expected_options[self.api_type]):
            raise ValueError(f"options do not match apiType '{self.api_type.value}'.")
        return self

    @property
    def api_key(self) -> str | None:
        return self.auth.api_key.get_secret_value() if self.auth.api_key else None

    def to_maf_options(self) -> dict[str, Any]:
        return self.options.to_maf_options()

    def public_dict(self) -> dict[str, Any]:
        payload = self.model_dump(
            mode="json",
            by_alias=True,
            exclude={"credential_revision"},
            exclude_none=True,
        )
        payload["auth"] = {
            "type": self.auth.type.value,
            "apiKeyConfigured": bool(self.auth.api_key),
        }
        return payload

    def storage_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json", by_alias=True, exclude_none=True)
        payload["auth"] = {"type": self.auth.type.value}
        if self.api_key:
            payload["auth"]["apiKey"] = self.api_key
        return payload

    def fingerprint(self) -> str:
        payload = self.public_dict()
        payload["credentialRevision"] = self.credential_revision
        canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class FoundrySettingsWrite(StrictModel):
    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    endpoint_kind: EndpointKind
    endpoint: str
    model: str
    api_type: ApiType
    version_mode: VersionMode
    api_version: str | None = None
    auth: AuthUpdate = Field(default_factory=AuthUpdate)
    agent_instructions: str = DEFAULT_AGENT_INSTRUCTIONS
    options: dict[str, Any] = Field(default_factory=dict)

    def resolve(self, existing: FoundrySettings | None) -> FoundrySettings:
        current_key = existing.api_key if existing else None
        revision = existing.credential_revision if existing else 0
        if self.auth.type == AuthType.ENTRA_ID:
            resolved_key = None
            if current_key is not None or (existing and existing.auth.type != AuthType.ENTRA_ID):
                revision += 1
        elif self.auth.api_key.action == SecretAction.SET:
            resolved_key = (self.auth.api_key.value or "").strip()
            revision += 1
        elif self.auth.api_key.action == SecretAction.CLEAR:
            resolved_key = None
            revision += 1
        else:
            resolved_key = current_key
        auth = AuthConfig(type=self.auth.type, api_key=resolved_key)
        return FoundrySettings.model_validate({
            "schemaVersion": SCHEMA_VERSION,
            "endpointKind": self.endpoint_kind,
            "endpoint": self.endpoint,
            "model": self.model,
            "apiType": self.api_type,
            "versionMode": self.version_mode,
            "apiVersion": self.api_version,
            "auth": auth,
            "agentInstructions": self.agent_instructions,
            "options": self.options,
            "credentialRevision": revision,
        })


def _validate_openai_metadata(value: dict[str, str] | None) -> dict[str, str] | None:
    if value is None:
        return None
    if len(value) > 16:
        raise ValueError("metadata supports at most 16 entries.")
    for key, item in value.items():
        if len(key) > 64:
            raise ValueError("metadata keys must be at most 64 characters.")
        if len(item) > 512:
            raise ValueError("metadata values must be at most 512 characters.")
    return value


def _normalize_url(value: str) -> tuple[str, str, str]:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise ValueError("Endpoint must be an absolute HTTP(S) URL.")
    if parsed.query or parsed.fragment:
        raise ValueError("Endpoint must not contain a query string or fragment.")
    return parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/")


def normalize_project_endpoint(value: str) -> str:
    scheme, netloc, path = _normalize_url(value)
    if not re.fullmatch(r"/api/projects/[^/]+", path, flags=re.IGNORECASE):
        raise ValueError("Project endpoint must end with /api/projects/{project-name}.")
    return urlunsplit((scheme, netloc, path, "", ""))


def normalize_model_endpoint(value: str) -> str:
    scheme, netloc, path = _normalize_url(value)
    lowered = path.lower()
    if "/api/projects/" in lowered:
        raise ValueError("A Project endpoint cannot be used as a Model endpoint.")
    cut_points = [position for suffix in ("/openai", "/anthropic") if (position := lowered.find(suffix)) >= 0]
    if cut_points:
        path = path[: min(cut_points)]
    path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, "", ""))


def openai_v1_base_url(settings: FoundrySettings) -> str:
    base = settings.endpoint.rstrip("/")
    return f"{base}/openai/v1/"


def claude_base_url(settings: FoundrySettings) -> str:
    return f"{settings.endpoint.rstrip('/')}/anthropic"


def _legacy_number(value: Any, cast: type[int] | type[float]) -> int | float | None:
    if value in (None, ""):
        return None
    try:
        return cast(value)
    except (TypeError, ValueError):
        return None


def migrate_legacy_config(data: Mapping[str, Any]) -> FoundrySettings | None:
    endpoint = str(data.get("endpoint") or "").strip()
    model = str(data.get("deployment") or data.get("model") or "").strip()
    if not endpoint or not model:
        return None
    raw_api = str(data.get("api_type") or data.get("apiType") or "chat").lower()
    api_type = ApiType.RESPONSES if raw_api in {"response", "responses"} else ApiType.CHAT_COMPLETIONS
    api_version = str(data.get("api_version") or data.get("apiVersion") or "").strip()
    version_mode = VersionMode.V1 if api_version.lower() == "v1" or not api_version else VersionMode.DATED
    key = str(data.get("api_key") or data.get("apiKey") or "").strip() or None
    common = {
        "temperature": _legacy_number(data.get("temperature"), float),
        "topP": _legacy_number(data.get("top_p", data.get("topP")), float),
    }
    if api_type == ApiType.RESPONSES:
        options = {
            **common,
            "maxOutputTokens": _legacy_number(
                data.get("max_output_tokens", data.get("max_tokens", data.get("maxTokens"))), int
            ),
            "reasoningEffort": data.get("reasoning_effort") or data.get("reasoningEffort") or None,
            "verbosity": data.get("verbosity") or None,
        }
    else:
        options = {
            **common,
            "maxCompletionTokens": _legacy_number(
                data.get("max_completion_tokens", data.get("max_tokens", data.get("maxTokens"))), int
            ),
        }
    options = {key_: value for key_, value in options.items() if value is not None}
    return FoundrySettings.model_validate({
        "schemaVersion": SCHEMA_VERSION,
        "endpointKind": "model",
        "endpoint": endpoint,
        "model": model,
        "apiType": api_type.value,
        "versionMode": version_mode.value,
        "apiVersion": api_version if version_mode == VersionMode.DATED else None,
        "auth": {"type": "api_key" if key else "entra_id", "apiKey": key},
        "agentInstructions": data.get("system_prompt") or data.get("systemPrompt") or DEFAULT_AGENT_INSTRUCTIONS,
        "options": options,
        "credentialRevision": 1 if key else 0,
    })


class FoundrySettingsStore:
    def __init__(self, path: Path, legacy_path: Path | None = None):
        self.path = path
        self.legacy_path = legacy_path

    def load(self) -> FoundrySettings | None:
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return FoundrySettings.model_validate(data)
        if self.legacy_path and self.legacy_path.exists():
            legacy = json.loads(self.legacy_path.read_text(encoding="utf-8"))
            migrated = migrate_legacy_config(legacy)
            if migrated is not None:
                self._backup_legacy()
                self.save(migrated)
            return migrated
        return None

    def save(self, settings: FoundrySettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(settings.storage_dict(), ensure_ascii=False, indent=2)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=str(self.path.parent),
            text=True,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.chmod(temporary_name, 0o600)
            except OSError:
                pass
            os.replace(temporary_name, self.path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

    def update(self, payload: FoundrySettingsWrite) -> FoundrySettings:
        existing = self.load()
        resolved = payload.resolve(existing)
        self.save(resolved)
        return resolved

    def _backup_legacy(self) -> None:
        if not self.legacy_path:
            return
        backup = self.legacy_path.with_suffix(self.legacy_path.suffix + ".pre-foundry.bak")
        if not backup.exists():
            backup.write_bytes(self.legacy_path.read_bytes())


FoundrySettingsAdapter = TypeAdapter(FoundrySettings)


__all__ = [
    "ApiType",
    "AuthType",
    "ChatCompletionsOptions",
    "ClaudeMessagesOptions",
    "DEFAULT_AGENT_INSTRUCTIONS",
    "EndpointKind",
    "FoundrySettings",
    "FoundrySettingsStore",
    "FoundrySettingsWrite",
    "ResponsesOptions",
    "SCHEMA_VERSION",
    "SecretAction",
    "VersionMode",
    "claude_base_url",
    "migrate_legacy_config",
    "normalize_model_endpoint",
    "normalize_project_endpoint",
    "openai_v1_base_url",
]
