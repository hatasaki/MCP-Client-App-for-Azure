from __future__ import annotations

import asyncio
import hashlib
import os
import re
import webbrowser
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List
from urllib.parse import urlsplit

import httpx
from agent_framework import FunctionTool, MCPStdioTool, MCPStreamableHTTPTool
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from pydantic import AnyUrl

_REMOTE_NAME_KEY = "_mcp_remote_name"


class InMemoryTokenStorage(TokenStorage):
    def __init__(self) -> None:
        self.tokens: OAuthToken | None = None
        self.client_info: OAuthClientInformationFull | None = None

    async def get_tokens(self) -> OAuthToken | None:
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self.tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self.client_info

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.client_info = client_info


class OAuthCallbackBroker:
    """Routes OAuth callbacks to a server-specific waiter."""

    def __init__(self) -> None:
        self._waiters: dict[str, asyncio.Future[tuple[str, str | None]]] = {}
        self._lock = asyncio.Lock()

    async def wait(self, server_id: str) -> tuple[str, str | None]:
        loop = asyncio.get_running_loop()
        async with self._lock:
            previous = self._waiters.pop(server_id, None)
            if previous and not previous.done():
                previous.cancel()
            future: asyncio.Future[tuple[str, str | None]] = loop.create_future()
            self._waiters[server_id] = future
        try:
            return await future
        finally:
            async with self._lock:
                if self._waiters.get(server_id) is future:
                    self._waiters.pop(server_id, None)

    def resolve(self, server_id: str, code: str | None, state: str | None) -> bool:
        future = self._waiters.get(server_id)
        if not future or future.done():
            return False
        future.set_result((code or "", state))
        return True

    def cancel(self, server_id: str) -> None:
        future = self._waiters.pop(server_id, None)
        if future and not future.done():
            future.cancel()

    def cancel_all(self) -> None:
        for server_id in list(self._waiters):
            self.cancel(server_id)


callback_broker = OAuthCallbackBroker()


def register_callback_result(*args: str | None) -> bool:
    """Deliver OAuth callback data.

    Preferred signature is ``(server_id, code, state)``. The legacy
    ``(code, state)`` form is accepted only when exactly one flow is pending.
    """
    if len(args) == 3:
        server_id, code, state = args
        return callback_broker.resolve(server_id or "", code, state)
    if len(args) == 2 and len(callback_broker._waiters) == 1:
        server_id = next(iter(callback_broker._waiters))
        return callback_broker.resolve(server_id, args[0], args[1])
    return False


async def _open_oauth_redirect(auth_url: str) -> None:
    try:
        opened = False if os.environ.get("MCPCLIENT_HEADLESS") == "1" else webbrowser.open(auth_url)
        if not opened:
            print(f"Open this URL to authorize the MCP server: {auth_url}")
    except Exception:
        print(f"Open this URL to authorize the MCP server: {auth_url}")


def _safe_slug(value: str, *, maximum: int = 40) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_-").lower() or "server"
    return slug[:maximum]


def _server_id(config: Dict[str, Any]) -> str:
    value = str(config.get("id") or config.get("name") or "").strip()
    if not value:
        raise ValueError("MCP server id or name is required.")
    return value


def _prefix_for(server_id: str) -> str:
    digest = hashlib.sha256(server_id.encode("utf-8")).hexdigest()[:8]
    return f"mcp_{_safe_slug(server_id)}_{digest}"


def _callback_id(server_id: str) -> str:
    digest = hashlib.sha256(server_id.encode("utf-8")).hexdigest()[:16]
    return f"{_safe_slug(server_id, maximum=24)}_{digest}"


@dataclass(slots=True)
class MCPRegistryEntry:
    server_id: str
    name: str
    config: Dict[str, Any]
    tool: MCPStdioTool | MCPStreamableHTTPTool
    http_client: httpx.AsyncClient | None = None

    async def close(self) -> None:
        callback_broker.cancel(_callback_id(self.server_id))
        try:
            await self.tool.close()
        finally:
            if self.http_client is not None:
                await self.http_client.aclose()


class MCPManager:
    """Atomic registry of MAF local MCP tools and their discovered functions."""

    def __init__(self, servers: List[Dict[str, Any]]):
        self._servers_conf = list(servers)
        self._entries: dict[str, MCPRegistryEntry] = {}
        self._qualified_functions: dict[str, FunctionTool] = {}
        self._model_functions: dict[str, FunctionTool] = {}
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> "MCPManager":
        for config in self._servers_conf:
            await self.add_server(config)
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    async def close(self) -> None:
        async with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
            self._qualified_functions.clear()
            self._model_functions.clear()
        callback_broker.cancel_all()
        await asyncio.gather(*(entry.close() for entry in entries), return_exceptions=True)

    @property
    def function_defs(self) -> list[dict[str, Any]]:
        definitions: list[dict[str, Any]] = []
        for entry in self._entries.values():
            for function in entry.tool.functions:
                remote_name = str((function.additional_properties or {}).get(_REMOTE_NAME_KEY) or function.name)
                qualified_id = f"{entry.server_id}:{remote_name}"
                definitions.append({
                    "id": qualified_id,
                    "qualifiedId": qualified_id,
                    "name": function.name,
                    "originalName": remote_name,
                    "displayName": f"{entry.name} › {remote_name}",
                    "description": function.description,
                    "parameters": function.parameters(),
                    "serverId": entry.server_id,
                    "serverName": entry.name,
                })
        return definitions

    def get_functions(self, qualified_ids: Iterable[str]) -> list[FunctionTool]:
        functions: list[FunctionTool] = []
        seen: set[str] = set()
        for qualified_id in qualified_ids:
            function = self._qualified_functions.get(qualified_id)
            if function is None:
                raise KeyError(f"MCP tool '{qualified_id}' is not available.")
            if function.name not in seen:
                functions.append(function)
                seen.add(function.name)
        return functions

    def get_function(self, qualified_id: str) -> FunctionTool:
        function = self._qualified_functions.get(qualified_id)
        if function is None:
            raise KeyError(f"MCP tool '{qualified_id}' is not available.")
        return function

    def qualified_id_for_model_name(self, model_name: str) -> str | None:
        for qualified_id, function in self._qualified_functions.items():
            if function.name == model_name:
                return qualified_id
        return None

    def tool_definition_for_model_name(self, model_name: str) -> dict[str, Any] | None:
        qualified_id = self.qualified_id_for_model_name(model_name)
        if qualified_id is None:
            return None
        return next(
            (item for item in self.function_defs if item["qualifiedId"] == qualified_id),
            None,
        )

    async def call_tool(self, qualified_id: str, args: Dict[str, Any] | None) -> Any:
        function = self.get_function(qualified_id)
        return await function.invoke(arguments=args or {})

    async def add_server(self, config: Dict[str, Any]) -> list[dict[str, Any]]:
        candidate = await self._connect_candidate(config)
        server_id = candidate.server_id
        try:
            async with self._lock:
                old = self._entries.get(server_id)
                self._entries[server_id] = candidate
                try:
                    self._rebuild_function_indexes()
                except BaseException:
                    if old is None:
                        self._entries.pop(server_id, None)
                    else:
                        self._entries[server_id] = old
                    self._rebuild_function_indexes()
                    raise
            if old is not None:
                await old.close()
            return [item for item in self.function_defs if item["serverId"] == server_id]
        except BaseException:
            await candidate.close()
            raise

    async def remove_server(self, identifier: str) -> None:
        async with self._lock:
            server_id = next(
                (
                    key
                    for key, entry in self._entries.items()
                    if key == identifier or entry.name == identifier
                ),
                None,
            )
            entry = self._entries.pop(server_id, None) if server_id else None
            self._rebuild_function_indexes()
        if entry is not None:
            await entry.close()

    def server_configs(self) -> list[dict[str, Any]]:
        return [dict(entry.config) for entry in self._entries.values()]

    async def _connect_candidate(self, config: Dict[str, Any]) -> MCPRegistryEntry:
        normalized = dict(config)
        server_id = _server_id(normalized)
        name = str(normalized.get("name") or server_id)
        normalized["id"] = server_id
        normalized["name"] = name
        transport = str(normalized.get("transport") or "http").lower()
        prefix = _prefix_for(server_id)
        if transport == "stdio":
            command = str(normalized.get("command") or "").strip()
            if not command:
                raise ValueError("command is required for an STDIO MCP server.")
            env = {str(key): str(value) for key, value in (normalized.get("env") or {}).items()}
            tool: MCPStdioTool | MCPStreamableHTTPTool = MCPStdioTool(
                name=name,
                command=command,
                args=[str(item) for item in normalized.get("args") or []],
                env=env or None,
                cwd=normalized.get("cwd") or None,
                tool_name_prefix=prefix,
                approval_mode="always_require",
            )
            http_client = None
        elif transport in {"http", "streamable", "streamable-http", "streamable_http", "sse"}:
            url = str(normalized.get("url") or "").strip()
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("A valid HTTP(S) URL is required for an HTTP MCP server.")
            # `sse` is retained as a UI/config alias for Streamable HTTP. Legacy SSE is not used.
            normalized["transport"] = "sse" if transport == "sse" else "http"
            headers = {str(key): str(value) for key, value in (normalized.get("headers") or {}).items()}
            oauth = self._oauth_provider(server_id, url)
            http_client = httpx.AsyncClient(
                headers=headers,
                auth=oauth,
                follow_redirects=True,
                timeout=httpx.Timeout(30, read=60 * 5),
            )
            tool = MCPStreamableHTTPTool(
                name=name,
                url=url,
                http_client=http_client,
                tool_name_prefix=prefix,
                approval_mode="always_require",
            )
        else:
            raise ValueError(f"Unsupported MCP transport: {transport}")

        entry = MCPRegistryEntry(
            server_id=server_id,
            name=name,
            config=normalized,
            tool=tool,
            http_client=http_client,
        )
        try:
            await tool.connect()
        except BaseException:
            await entry.close()
            raise
        return entry

    def _oauth_provider(self, server_id: str, url: str) -> OAuthClientProvider:
        parsed = urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        callback_id = _callback_id(server_id)
        callback_base_url = os.environ.get(
            "MCPCLIENT_CALLBACK_BASE_URL",
            "http://127.0.0.1:3001",
        ).rstrip("/")
        callback_url = f"{callback_base_url}/callback/{callback_id}"

        async def wait_for_callback() -> tuple[str, str | None]:
            timeout_seconds = float(os.environ.get("MCPCLIENT_OAUTH_TIMEOUT_SECONDS", "300"))
            try:
                return await asyncio.wait_for(
                    callback_broker.wait(callback_id),
                    timeout=timeout_seconds,
                )
            except TimeoutError as exc:
                raise TimeoutError("MCP OAuth authorization timed out. Please try again.") from exc

        return OAuthClientProvider(
            server_url=origin,
            client_metadata=OAuthClientMetadata(
                client_name="MCP Client for Microsoft Foundry",
                redirect_uris=[AnyUrl(callback_url)],
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
                client_uri=AnyUrl(callback_base_url),
            ),
            storage=InMemoryTokenStorage(),
            redirect_handler=_open_oauth_redirect,
            callback_handler=wait_for_callback,
        )

    def _rebuild_function_indexes(self) -> None:
        qualified: dict[str, FunctionTool] = {}
        model_names: dict[str, FunctionTool] = {}
        for entry in self._entries.values():
            for function in entry.tool.functions:
                remote_name = str((function.additional_properties or {}).get(_REMOTE_NAME_KEY) or function.name)
                qualified_id = f"{entry.server_id}:{remote_name}"
                if qualified_id in qualified:
                    raise ValueError(f"Duplicate qualified MCP tool id: {qualified_id}")
                if function.name in model_names:
                    raise ValueError(f"Duplicate model MCP tool name: {function.name}")
                qualified[qualified_id] = function
                model_names[function.name] = function
        self._qualified_functions = qualified
        self._model_functions = model_names


__all__ = [
    "InMemoryTokenStorage",
    "MCPManager",
    "OAuthCallbackBroker",
    "callback_broker",
    "register_callback_result",
]
