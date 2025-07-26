from typing import Any, Dict, List
import asyncio
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.client.sse import sse_client
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from urllib.parse import urlparse
from pydantic import AnyUrl
import webbrowser
from app_runner import HOST, PORT

# Future for receiving OAuth callback via existing FastAPI server
callback_future: asyncio.Future[tuple[str, str | None]] | None = None
def register_callback_result(code: str | None, state: str | None) -> None:
    """Called by backend /callback route to deliver OAuth code and state."""
    global callback_future
    if callback_future and not callback_future.done():
        callback_future.set_result((code, state))

class InMemoryTokenStorage(TokenStorage):
    """Demo In-memory token storage implementation."""

    def __init__(self):
        self.tokens: OAuthToken | None = None
        self.client_info: OAuthClientInformationFull | None = None

    async def get_tokens(self) -> OAuthToken | None:
        """Get stored tokens."""
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        """Store tokens."""
        self.tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        """Get stored client information."""
        return self.client_info

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        """Store client information."""
        self.client_info = client_info

async def handle_redirect(auth_url: str) -> None:
    try:
        webbrowser.open(auth_url)
        print(f"Opened browser to: {auth_url}")
    except Exception as e:
        print(f"Failed to open browser ({e}), please visit manually:")
        print(f"Visit: {auth_url}")

async def handle_callback() -> tuple[str, str | None]:
    """Wait for OAuth callback delivered via backend /callback endpoint."""
    global callback_future
    loop = asyncio.get_event_loop()
    callback_future = loop.create_future()
    print("Waiting for OAuth callback at http://localhost:3001/callback ...")
    # The backend must call register_callback_result when /callback is hit
    code, state = await callback_future
    return code or "", state

class MCPManager:
    """Connects to configured MCP servers and exposes their tools."""

    def __init__(self, servers: List[Dict[str, Any]]):
        self._servers_conf = servers
        self._stack: AsyncExitStack | None = None
        self.tool_to_session: Dict[str, ClientSession] = {}
        self.function_defs: List[Dict[str, Any]] = []
        self.session_to_server_name: Dict[ClientSession, str] = {}

    async def __aenter__(self):
        self._stack = AsyncExitStack()
        await self._stack.__aenter__()
        await self._connect_all()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._stack:
            await self._stack.__aexit__(exc_type, exc_val, exc_tb)

    async def _connect_all(self):
        for srv in self._servers_conf:
            name = srv.get("name", "Unnamed MCP Server")
            transport = srv.get("transport", "http").lower()
            print(f"🔗 Connecting to {name} ({transport})...")
            try:
                if transport == "stdio":
                    await self._connect_stdio(name, srv)
                elif transport in {"http", "streamable", "streamable-http", "stream", "streamable_http"}:
                    await self._connect_streamable_http(name, srv)
                elif transport == "sse":
                    await self._connect_sse(name, srv)
                else:
                    print(f"⚠️ Unsupported transport: {transport} ({name}) — skipping")
            except Exception as e:
                print(f"❌ Connection to {name} failed: {e}")

    async def _connect_stdio(self, name: str, cfg: Dict[str, Any]):
        cmd = cfg.get("command")
        if not cmd:
            raise ValueError("command not set")
        params = StdioServerParameters(command=cmd, args=cfg.get("args", []), env=cfg.get("env"))
        read, write = await self._stack.enter_async_context(stdio_client(params))
        session = await self._stack.enter_async_context(ClientSession(read, write))
        self.session_to_server_name[session] = name
        tool_count = await self._register_session(session)
        print(f"✅ Connected to {name} (stdio) — {tool_count} tools")

    async def _oauth_provider(self, url: str) -> OAuthClientProvider:
        base_url = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        client_url = f"http://{HOST}:{PORT}"
        oauth_auth = OAuthClientProvider(
            server_url=base_url,
            client_metadata=OAuthClientMetadata(
                client_name="MCP Client for Azure",
                redirect_uris=[AnyUrl(f"{client_url}/callback")],
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
                client_uri=AnyUrl(client_url),
            ),
            storage=InMemoryTokenStorage(),
            redirect_handler=handle_redirect,
            callback_handler=handle_callback,
        )
        return oauth_auth

    async def _connect_streamable_http(self, name: str, cfg: Dict[str, Any]):
        url = cfg.get("url")
        if not url:
            raise ValueError("url not set")
        oauth_auth = await self._oauth_provider(url)
        read, write, _ = await self._stack.enter_async_context(
            streamablehttp_client(url, headers=cfg.get("headers") or None, auth=oauth_auth)
        )
        session = await self._stack.enter_async_context(ClientSession(read, write))
        self.session_to_server_name[session] = name
        tool_count = await self._register_session(session)
        print(f"✅ Connected to {name} (streamable-http) — {tool_count} tools")

    async def _connect_sse(self, name: str, cfg: Dict[str, Any]):
        url = cfg.get("url")
        if not url:
            raise ValueError("url not set")
        oauth_auth = await self._oauth_provider(url)
        read, write = await self._stack.enter_async_context(
            sse_client(url, headers=cfg.get("headers") or None, auth=oauth_auth)
        )
        session = await self._stack.enter_async_context(ClientSession(read, write))
        self.session_to_server_name[session] = name
        tool_count = await self._register_session(session)
        print(f"✅ Connected to {name} (SSE) — {tool_count} tools — recommended: Streamable HTTP")

    async def _register_session(self, session: ClientSession) -> int:
        await session.initialize()
        tool_list = await session.list_tools()
        for t in tool_list.tools:
            if t.name in self.tool_to_session:
                continue
            self.tool_to_session[t.name] = session
            self.function_defs.append({
                "name": t.name,
                "description": getattr(t, "description", ""),
                "parameters": getattr(t, "inputSchema", {"type": "object", "properties": {}}),
                "serverId": self.session_to_server_name.get(session, "")
            })
        return len(tool_list.tools)

    async def call_tool(self, name: str, args: Dict[str, Any] | None):
        if name not in self.tool_to_session:
            raise KeyError(f"Tool '{name}' not registered")
        return await self.tool_to_session[name].call_tool(name, args or {})

    async def add_server(self, cfg: Dict[str, Any]):
        """Add and connect a single MCP server at runtime. Returns list of tools for that server."""
        self._servers_conf.append(cfg)
        name = cfg.get("name", "Unnamed MCP Server")
        transport = cfg.get("transport", "http").lower()
        existing_tool_names = set(fd["name"] for fd in self.function_defs)
        if transport == "stdio":
            await self._connect_stdio(name, cfg)
        elif transport in {"http", "streamable", "streamable-http", "stream", "streamable_http"}:
            await self._connect_streamable_http(name, cfg)
        elif transport == "sse":
            await self._connect_sse(name, cfg)
        else:
            raise ValueError(f"Unsupported transport: {transport}")

        # Identify tools that were not present before this call **and** belong to this server.
        new_tools = [
            fd for fd in self.function_defs
            if fd["name"] not in existing_tool_names and fd.get("serverId") == name
        ]
        return new_tools

    def _rebuild_function_defs(self):
        """Recreate function_defs from remaining tool_to_session."""
        self.function_defs = []
        for tname, sess in self.tool_to_session.items():
            self.function_defs.append({
                "name": tname,
                "description": "",
                "parameters": {"type": "object", "properties": {}},
                "serverId": self.session_to_server_name.get(sess, "")
            })

    def remove_server(self, name: str):
        """Remove a server and its tools from internal registries (does not close connections)."""
        # 1. Remove from config list
        self._servers_conf = [c for c in self._servers_conf if c.get("name") != name]

        # 2. Identify sessions belonging to the server being removed
        sessions_to_remove = [s for s, n in self.session_to_server_name.items() if n == name]

        # 3. Delete mapping entries for those sessions
        for s in sessions_to_remove:
            self.session_to_server_name.pop(s, None)

        # 4. Remove tools and their session mapping that belong to the removed sessions
        self.tool_to_session = {
            tool_name: sess for tool_name, sess in self.tool_to_session.items() if sess not in sessions_to_remove
        }

        # 5. Simply filter out the removed server's tools from function_defs to preserve existing metadata
        self.function_defs = [fd for fd in self.function_defs if fd.get("serverId") != name]
