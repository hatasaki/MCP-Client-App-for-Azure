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
    ValidationError,
    field_validator,
    model_validator,
)

from app.secret_protection import SecretProtectionError, SecretProtector

SCHEMA_VERSION = 3

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
                options.setdefault("tool_choice", "auto")
            elif key == "metadata_user_id":
                options["metadata"] = {"user_id": value}
            elif key != "max_tokens":
                options[key] = value
        return options


OptionsType = ResponsesOptions | ChatCompletionsOptions | ClaudeMessagesOptions


class ModelSelection(StrictModel):
    api_type: ApiType
    model: str

    def key(self) -> str:
        return f"{self.api_type.value}:{self.model}"


class ApiProfileBase(StrictModel):
    models: Annotated[list[str], Field(min_length=1)]
    default_model: str
    version_mode: VersionMode
    api_version: str | None = None

    @field_validator("models")
    @classmethod
    def validate_models(cls, value: list[str]) -> list[str]:
        if any(not model for model in value):
            raise ValueError("Model deployment names must not be empty.")
        if len(value) != len(set(value)):
            raise ValueError("Model deployment names must be unique within an API type.")
        return value

    @model_validator(mode="after")
    def validate_default_model(self) -> "ApiProfileBase":
        if self.default_model not in self.models:
            raise ValueError("defaultModel must reference a configured model deployment.")
        return self

    def to_maf_options(self) -> dict[str, Any]:
        raise NotImplementedError


class ResponsesProfile(ApiProfileBase):
    api_type: Literal[ApiType.RESPONSES] = ApiType.RESPONSES
    options: ResponsesOptions = Field(default_factory=ResponsesOptions)

    def to_maf_options(self) -> dict[str, Any]:
        return self.options.to_maf_options()


class ChatCompletionsProfile(ApiProfileBase):
    api_type: Literal[ApiType.CHAT_COMPLETIONS] = ApiType.CHAT_COMPLETIONS
    options: ChatCompletionsOptions = Field(default_factory=ChatCompletionsOptions)

    def to_maf_options(self) -> dict[str, Any]:
        return self.options.to_maf_options()


class ClaudeMessagesProfile(ApiProfileBase):
    api_type: Literal[ApiType.CLAUDE_MESSAGES] = ApiType.CLAUDE_MESSAGES
    options: ClaudeMessagesOptions

    def to_maf_options(self) -> dict[str, Any]:
        return self.options.to_maf_options()


ApiProfile = Annotated[
    ResponsesProfile | ChatCompletionsProfile | ClaudeMessagesProfile,
    Field(discriminator="api_type"),
]


class ResolvedFoundrySettings(StrictModel):
    endpoint_kind: EndpointKind
    endpoint: str
    model: str
    api_type: ApiType
    version_mode: VersionMode
    api_version: str | None = None
    auth: AuthConfig
    agent_instructions: str
    options: OptionsType
    credential_revision: Annotated[int, Field(ge=0)] = 0

    @property
    def api_key(self) -> str | None:
        return self.auth.api_key.get_secret_value() if self.auth.api_key else None

    @property
    def selection(self) -> ModelSelection:
        return ModelSelection(api_type=self.api_type, model=self.model)

    def to_maf_options(self) -> dict[str, Any]:
        return self.options.to_maf_options()

    def public_dict(self) -> dict[str, Any]:
        payload = self.model_dump(
            mode="json",
            by_alias=True,
            exclude={"credential_revision", "auth"},
            exclude_none=True,
        )
        payload["auth"] = {"type": self.auth.type.value, "apiKeyConfigured": bool(self.auth.api_key)}
        return payload

    def fingerprint(self) -> str:
        payload = self.public_dict()
        payload["credentialRevision"] = self.credential_revision
        canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _upgrade_v2_payload(data: Mapping[str, Any]) -> dict[str, Any]:
    mutable = dict(data)
    model = str(mutable.pop("model", "")).strip()
    api_type = str(mutable.pop("apiType", mutable.pop("api_type", "responses")))
    version_mode = mutable.pop("versionMode", mutable.pop("version_mode", "v1"))
    api_version = mutable.pop("apiVersion", mutable.pop("api_version", None))
    options = mutable.pop("options", {})
    if not model:
        raise ValueError("The v2 settings file does not contain a model deployment name.")
    mutable["schemaVersion"] = SCHEMA_VERSION
    mutable["apiProfiles"] = [{
        "apiType": api_type,
        "models": [model],
        "defaultModel": model,
        "versionMode": version_mode,
        "apiVersion": api_version,
        "options": options,
    }]
    mutable["defaultSelection"] = {"apiType": api_type, "model": model}
    return mutable


class FoundrySettings(StrictModel):
    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    endpoint_kind: EndpointKind
    endpoint: str
    auth: AuthConfig = Field(default_factory=AuthConfig)
    agent_instructions: str = DEFAULT_AGENT_INSTRUCTIONS
    api_profiles: Annotated[list[ApiProfile], Field(min_length=1)]
    default_selection: ModelSelection
    credential_revision: Annotated[int, Field(ge=0)] = 0

    @model_validator(mode="before")
    @classmethod
    def accept_v2_shape(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        if "apiProfiles" not in data and "api_profiles" not in data and ("model" in data):
            return _upgrade_v2_payload(data)
        return data

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        if not value:
            raise ValueError("Endpoint is required.")
        return value

    @model_validator(mode="after")
    def validate_matrix(self) -> "FoundrySettings":
        profile_types = [profile.api_type for profile in self.api_profiles]
        if len(profile_types) != len(set(profile_types)):
            raise ValueError("Only one configuration may be stored for each API type.")

        if self.endpoint_kind == EndpointKind.PROJECT:
            object.__setattr__(self, "endpoint", normalize_project_endpoint(self.endpoint))
            if profile_types != [ApiType.RESPONSES]:
                raise ValueError("Project endpoints support a Responses API configuration only.")
            if self.auth.type != AuthType.ENTRA_ID:
                raise ValueError(
                    "Project endpoints use Entra ID with FoundryChatClient. Select a Model endpoint to use an API key."
                )
        else:
            object.__setattr__(self, "endpoint", normalize_model_endpoint(self.endpoint))
        if not self.endpoint.startswith("https://"):
            raise ValueError("Microsoft Foundry endpoints require HTTPS.")

        for profile in self.api_profiles:
            if self.endpoint_kind == EndpointKind.PROJECT:
                if profile.version_mode != VersionMode.V1 or profile.api_version is not None:
                    raise ValueError("Project endpoints use v1 and do not accept a dated API version.")
            elif profile.api_type == ApiType.CLAUDE_MESSAGES:
                if profile.version_mode != VersionMode.PROVIDER or profile.api_version is not None:
                    raise ValueError("Claude Messages uses provider version mode without apiVersion.")
            elif profile.version_mode == VersionMode.PROVIDER:
                raise ValueError("Provider version mode is valid only for Claude Messages.")
            elif profile.version_mode == VersionMode.DATED:
                if not profile.api_version or not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:-preview)?", profile.api_version):
                    raise ValueError("A dated API version such as 2025-04-01-preview is required.")
            elif profile.api_version is not None:
                raise ValueError("apiVersion must be omitted when versionMode is v1.")

        self.resolve(self.default_selection)
        return self

    @property
    def api_key(self) -> str | None:
        return self.auth.api_key.get_secret_value() if self.auth.api_key else None

    def get_profile(self, api_type: ApiType | str) -> ApiProfile:
        normalized = ApiType(api_type)
        profile = next((item for item in self.api_profiles if item.api_type == normalized), None)
        if profile is None:
            raise KeyError(f"API type '{normalized.value}' is not configured.")
        return profile

    def resolve(self, selection: ModelSelection | Mapping[str, Any] | None = None) -> ResolvedFoundrySettings:
        selected = self.default_selection if selection is None else (
            selection if isinstance(selection, ModelSelection) else ModelSelection.model_validate(selection)
        )
        profile = self.get_profile(selected.api_type)
        if selected.model not in profile.models:
            raise KeyError(
                f"Model deployment '{selected.model}' is not configured for API type '{selected.api_type.value}'."
            )
        return ResolvedFoundrySettings(
            endpoint_kind=self.endpoint_kind,
            endpoint=self.endpoint,
            model=selected.model,
            api_type=selected.api_type,
            version_mode=profile.version_mode,
            api_version=profile.api_version,
            auth=self.auth,
            agent_instructions=self.agent_instructions,
            options=profile.options,
            credential_revision=self.credential_revision,
        )

    def selection_exists(self, selection: ModelSelection | Mapping[str, Any] | None) -> bool:
        if selection is None:
            return False
        try:
            self.resolve(selection)
            return True
        except (KeyError, ValueError):
            return False

    def available_selections(self) -> list[ModelSelection]:
        return [
            ModelSelection(api_type=profile.api_type, model=model)
            for profile in self.api_profiles
            for model in profile.models
        ]

    # Compatibility properties use the configured default selection.
    @property
    def model(self) -> str:
        return self.default_selection.model

    @property
    def api_type(self) -> ApiType:
        return self.default_selection.api_type

    @property
    def version_mode(self) -> VersionMode:
        return self.get_profile(self.api_type).version_mode

    @property
    def api_version(self) -> str | None:
        return self.get_profile(self.api_type).api_version

    @property
    def options(self) -> OptionsType:
        return self.get_profile(self.api_type).options

    def to_maf_options(self) -> dict[str, Any]:
        return self.resolve().to_maf_options()

    def fingerprint(self) -> str:
        return self.resolve().fingerprint()

    def public_dict(self) -> dict[str, Any]:
        payload = self.model_dump(
            mode="json",
            by_alias=True,
            exclude={"credential_revision", "auth"},
            exclude_none=True,
        )
        payload["auth"] = {"type": self.auth.type.value, "apiKeyConfigured": bool(self.auth.api_key)}
        return payload

    def storage_dict(self, protector: SecretProtector) -> dict[str, Any]:
        payload = self.model_dump(
            mode="json",
            by_alias=True,
            exclude={"auth"},
            exclude_none=True,
        )
        payload["auth"] = {"type": self.auth.type.value}
        if self.api_key:
            payload["auth"]["apiKeyEncrypted"] = protector.encrypt(
                self.api_key,
                context=_storage_encryption_context(payload),
            )
        return payload


class FoundrySettingsWrite(StrictModel):
    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    endpoint_kind: EndpointKind
    endpoint: str
    auth: AuthUpdate = Field(default_factory=AuthUpdate)
    agent_instructions: str = DEFAULT_AGENT_INSTRUCTIONS
    api_profiles: Annotated[list[ApiProfile], Field(min_length=1)]
    default_selection: ModelSelection

    @model_validator(mode="before")
    @classmethod
    def accept_v2_shape(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        if "apiProfiles" not in data and "api_profiles" not in data and "model" in data:
            return _upgrade_v2_payload(data)
        return data

    def resolve(self, existing: FoundrySettings | None) -> FoundrySettings:
        current_key = existing.api_key if existing else None
        revision = existing.credential_revision if existing else 0
        previous_auth_type = existing.auth.type if existing else None
        if self.auth.type == AuthType.ENTRA_ID:
            resolved_key = None
            if current_key is not None or previous_auth_type not in {None, AuthType.ENTRA_ID}:
                revision += 1
        elif self.auth.api_key.action == SecretAction.SET:
            resolved_key = (self.auth.api_key.value or "").strip()
            revision += 1
        elif self.auth.api_key.action == SecretAction.CLEAR:
            resolved_key = None
            revision += 1
        else:
            resolved_key = current_key
            if previous_auth_type not in {None, AuthType.API_KEY}:
                revision += 1
        auth = AuthConfig(type=self.auth.type, api_key=resolved_key)
        return FoundrySettings(
            endpoint_kind=self.endpoint_kind,
            endpoint=self.endpoint,
            auth=auth,
            agent_instructions=self.agent_instructions,
            api_profiles=self.api_profiles,
            default_selection=self.default_selection,
            credential_revision=revision,
        )


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
    return urlunsplit((scheme, netloc, path.rstrip("/"), "", ""))


def openai_v1_base_url(settings: ResolvedFoundrySettings | FoundrySettings) -> str:
    return f"{settings.endpoint.rstrip('/')}/openai/v1/"


def claude_base_url(settings: ResolvedFoundrySettings | FoundrySettings) -> str:
    return f"{settings.endpoint.rstrip('/')}/anthropic"


def _legacy_number(value: Any, cast: type[int] | type[float]) -> int | float | None:
    if value in (None, ""):
        return None
    try:
        return cast(value)
    except (TypeError, ValueError):
        return None


def migrate_v2_config(data: Mapping[str, Any]) -> FoundrySettings:
    return FoundrySettings.model_validate(_upgrade_v2_payload(data))


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
    options = {name: value for name, value in options.items() if value is not None}
    return FoundrySettings.model_validate({
        "schemaVersion": SCHEMA_VERSION,
        "endpointKind": "model",
        "endpoint": endpoint,
        "auth": {"type": "api_key" if key else "entra_id", "apiKey": key},
        "agentInstructions": data.get("system_prompt") or data.get("systemPrompt") or DEFAULT_AGENT_INSTRUCTIONS,
        "apiProfiles": [{
            "apiType": api_type.value,
            "models": [model],
            "defaultModel": model,
            "versionMode": version_mode.value,
            "apiVersion": api_version if version_mode == VersionMode.DATED else None,
            "options": options,
        }],
        "defaultSelection": {"apiType": api_type.value, "model": model},
        "credentialRevision": 1 if key else 0,
    })


class FoundrySettingsStore:
    def __init__(
        self,
        path: Path,
        legacy_path: Path | None = None,
        protector: SecretProtector | None = None,
    ):
        self.path = path
        self.legacy_path = legacy_path
        self.protector = protector or SecretProtector()

    def load(self) -> FoundrySettings | None:
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, Mapping):
                raise ValueError("Foundry settings root must be a JSON object.")
            schema_version = int(data.get("schemaVersion", 2))
            if schema_version < SCHEMA_VERSION:
                settings = migrate_v2_config(data)
                self.save(settings)
                return settings
            settings = FoundrySettings.model_validate(self._decrypt_storage_payload(data))
            self._remove_plaintext_legacy_files()
            return settings
        if self.legacy_path and self.legacy_path.exists():
            legacy = json.loads(self.legacy_path.read_text(encoding="utf-8"))
            if not isinstance(legacy, Mapping):
                raise ValueError("Legacy settings root must be a JSON object.")
            migrated = migrate_legacy_config(legacy)
            if migrated is not None:
                self.save(migrated)
            return migrated
        return None

    def save(self, settings: FoundrySettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(settings.storage_dict(self.protector), ensure_ascii=False, indent=2)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=str(self.path.parent), text=True
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
        self._remove_plaintext_legacy_files()

    def update(self, payload: FoundrySettingsWrite) -> FoundrySettings:
        try:
            existing = self.load()
        except (SecretProtectionError, ValidationError, ValueError, TypeError, OSError, json.JSONDecodeError) as exc:
            can_replace_unreadable_key = (
                payload.auth.type == AuthType.ENTRA_ID
                or payload.auth.api_key.action == SecretAction.SET
            )
            if not can_replace_unreadable_key:
                if isinstance(exc, SecretProtectionError):
                    raise
                raise SecretProtectionError(
                    "Stored Foundry settings are invalid or corrupted. Replace the complete settings."
                ) from exc
            try:
                existing = self.load_recoverable_settings()
            except (ValidationError, ValueError, TypeError, OSError, json.JSONDecodeError):
                existing = None
        resolved = payload.resolve(existing)
        self.save(resolved)
        return resolved

    def load_recoverable_settings(self) -> FoundrySettings | None:
        """Recover validated non-secret fields while replacing an unreadable key."""
        if not self.path.exists():
            return None
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, Mapping):
            raise ValueError("Foundry settings root must be a JSON object.")
        if int(data.get("schemaVersion", 0)) != SCHEMA_VERSION:
            return None
        mutable = dict(data)
        auth = dict(mutable.get("auth") or {})
        auth.pop("apiKeyEncrypted", None)
        # A placeholder exists only in memory so the complete settings object,
        # including credentialRevision, can be validated and retained.
        auth["apiKey"] = "__unreadable_api_key__" if auth.get("type") == AuthType.API_KEY.value else None
        mutable["auth"] = auth
        return FoundrySettings.model_validate(mutable)

    def _decrypt_storage_payload(self, data: Mapping[str, Any]) -> dict[str, Any]:
        mutable = dict(data)
        auth = dict(mutable.get("auth") or {})
        if "apiKey" in auth:
            raise ValueError("Schema v3 settings must not contain a plaintext apiKey.")
        encrypted = auth.pop("apiKeyEncrypted", None)
        if auth.get("type") == AuthType.API_KEY.value:
            if not isinstance(encrypted, Mapping):
                raise ValueError("Encrypted API key data is required for API key authentication.")
            context_payload = dict(mutable)
            context_payload["auth"] = {"type": auth.get("type")}
            auth["apiKey"] = self.protector.decrypt(
                encrypted,
                context=_storage_encryption_context(context_payload),
            )
        elif encrypted is not None:
            raise ValueError("Encrypted API key data must be omitted for Entra ID authentication.")
        mutable["auth"] = auth
        return mutable

    def _remove_plaintext_legacy_files(self) -> None:
        if not self.legacy_path:
            return
        for path in (
            self.legacy_path,
            self.legacy_path.with_suffix(self.legacy_path.suffix + ".pre-foundry.bak"),
        ):
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                raise SecretProtectionError(
                    f"Encrypted settings were saved, but plaintext legacy file '{path.name}' could not be deleted."
                ) from exc


def _storage_encryption_context(payload: Mapping[str, Any]) -> bytes:
    """Bind ciphertext to every persisted non-secret setting."""
    context = {key: value for key, value in payload.items() if key != "auth"}
    context["auth"] = {"type": (payload.get("auth") or {}).get("type")}
    canonical = json.dumps(context, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return canonical.encode("utf-8")


FoundrySettingsAdapter = TypeAdapter(FoundrySettings)


__all__ = [
    "ApiProfile",
    "ApiType",
    "AuthType",
    "ChatCompletionsOptions",
    "ChatCompletionsProfile",
    "ClaudeMessagesOptions",
    "ClaudeMessagesProfile",
    "DEFAULT_AGENT_INSTRUCTIONS",
    "EndpointKind",
    "FoundrySettings",
    "FoundrySettingsStore",
    "FoundrySettingsWrite",
    "ModelSelection",
    "ResolvedFoundrySettings",
    "ResponsesOptions",
    "ResponsesProfile",
    "SCHEMA_VERSION",
    "SecretAction",
    "VersionMode",
    "claude_base_url",
    "migrate_legacy_config",
    "migrate_v2_config",
    "normalize_model_endpoint",
    "normalize_project_endpoint",
    "openai_v1_base_url",
]
