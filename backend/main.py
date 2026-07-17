from __future__ import annotations

import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import socketio
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from app.agent_runtime import (
    AgentRunBusyError,
    AgentRuntime,
    ApprovalNotFoundError,
)
from app.config import (
    load_foundry_settings,
    update_foundry_settings,
)
from app.foundry_config import FoundrySettings, FoundrySettingsWrite
from app.mcp_manager import MCPManager, register_callback_result
from app.saved_servers_manager import SavedServersManager
from app.session_manager import SessionManager

logger = logging.getLogger("mcpclient.backend")

session_manager = SessionManager()
saved_servers_manager = SavedServersManager()
mcp_manager: MCPManager | None = None
agent_runtime: AgentRuntime | None = None
foundry_settings: FoundrySettings | None = None
server_statuses: dict[str, dict[str, Any]] = {}
socket_active_sessions: dict[str, set[str]] = {}


@asynccontextmanager
async def app_lifespan(_app: FastAPI):
    global mcp_manager, agent_runtime, foundry_settings
    foundry_settings = load_foundry_settings()
    mcp_manager = await MCPManager([]).__aenter__()
    agent_runtime = AgentRuntime(session_manager, mcp_manager)
    try:
        yield
    finally:
        if agent_runtime:
            await agent_runtime.shutdown()
        if mcp_manager:
            await mcp_manager.close()


fastapi_app = FastAPI(title="MCP Client for Microsoft Foundry", lifespan=app_lifespan)
allowed_origins = [
    origin.strip()
    for origin in os.environ.get("MCPCLIENT_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]
if allowed_origins:
    fastapi_app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_methods=["GET", "PUT", "OPTIONS"],
        allow_headers=["Content-Type"],
    )

BUILD_DIR = Path(__file__).resolve().parent.parent / "client" / "build"
STATIC_DIR = BUILD_DIR / "static"
if STATIC_DIR.exists():
    fastapi_app.mount("/static", StaticFiles(directory=str(STATIC_DIR), html=False), name="static")
    index_path = BUILD_DIR / "index.html"
    if index_path.exists():

        @fastapi_app.get("/")
        async def serve_root() -> FileResponse:
            return FileResponse(index_path)


sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins=allowed_origins or None,
)
app = socketio.ASGIApp(sio, other_asgi_app=fastapi_app)


def _chat_error_payload(
    *,
    request_id: str,
    session_id: str,
    code: str,
    message: str,
    message_id: str = "",
    epoch: int = 0,
    sequence: int = 1,
    content: str = "",
    session: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "requestId": request_id,
        "sessionId": session_id,
        "messageId": message_id,
        "epoch": epoch,
        "sequence": sequence,
        "code": code,
        "message": message,
        "content": content,
    }
    if session is not None:
        payload["session"] = session
    return payload


@fastapi_app.get("/healthz")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@sio.event
async def connect(sid: str, environ: dict[str, Any]) -> None:
    socket_active_sessions.setdefault(sid, set())
    logger.info("Client connected: %s", sid)


@sio.event
async def disconnect(sid: str) -> None:
    sessions = socket_active_sessions.pop(sid, set())
    if agent_runtime:
        await asyncio.gather(
            *(agent_runtime.cancel(session_id) for session_id in sessions),
            return_exceptions=True,
        )
    logger.info("Client disconnected: %s", sid)


# ---------------------------------------------------------------------------
# Foundry settings REST API
# ---------------------------------------------------------------------------


@fastapi_app.get("/foundry-settings/status")
async def foundry_settings_status() -> dict[str, Any]:
    return {
        "isConfigured": foundry_settings is not None,
        "schemaVersion": foundry_settings.schema_version if foundry_settings else 2,
    }


@fastapi_app.get("/foundry-settings")
async def get_foundry_settings() -> dict[str, Any]:
    if foundry_settings is None:
        raise HTTPException(status_code=404, detail="Microsoft Foundry settings are not configured.")
    return foundry_settings.public_dict()


@fastapi_app.put("/foundry-settings")
async def set_foundry_settings(payload: FoundrySettingsWrite) -> dict[str, Any]:
    global foundry_settings
    try:
        foundry_settings = update_foundry_settings(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.errors(
                include_url=False,
                include_context=False,
                include_input=False,
            ),
        ) from exc
    public = foundry_settings.public_dict()
    await sio.emit("foundrySettingsUpdated", public)
    return public


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


@sio.event
async def createNewSession(sid: str) -> None:
    session = session_manager.create(str(uuid.uuid4()))
    await sio.emit("sessionCreated", session, room=sid)


@sio.event
async def getSessions(sid: str) -> None:
    await sio.emit("sessions", session_manager.list(), room=sid)


@sio.event
async def loadSession(sid: str, sessionId: str) -> None:
    session = session_manager.get(sessionId)
    if session:
        await sio.emit("sessionLoaded", session, room=sid)
    else:
        await sio.emit("error", {"message": "Session not found"}, room=sid)


@sio.event
async def deleteSession(sid: str, sessionId: str) -> None:
    if agent_runtime:
        await agent_runtime.cancel(sessionId)
    session_manager.delete(sessionId)
    await sio.emit("sessions", session_manager.list())


# ---------------------------------------------------------------------------
# Streaming MAF chat contract
# ---------------------------------------------------------------------------


@sio.on("chat:send")
async def chat_send(sid: str, data: dict[str, Any]) -> None:
    data = data if isinstance(data, dict) else {}
    session_id = str(data.get("sessionId") or "")
    request_id = str(data.get("requestId") or uuid.uuid4())
    if foundry_settings is None:
        await sio.emit(
            "chat:error",
            _chat_error_payload(
                request_id=request_id,
                session_id=session_id,
                code="SettingsMissing",
                message="Microsoft Foundry settings are not configured.",
            ),
            room=sid,
        )
        return
    if agent_runtime is None:
        await sio.emit(
            "chat:error",
            _chat_error_payload(
                request_id=request_id,
                session_id=session_id,
                code="RuntimeUnavailable",
                message="Agent runtime is not available.",
            ),
            room=sid,
        )
        return

    message = data.get("message")
    selected_tool_ids = data.get("selectedToolIds", [])
    if (
        not isinstance(message, str)
        or not message.strip()
        or not isinstance(selected_tool_ids, list)
        or any(not isinstance(item, str) for item in selected_tool_ids)
    ):
        await sio.emit(
            "chat:error",
            _chat_error_payload(
                request_id=request_id,
                session_id=session_id,
                code="InvalidRequest",
                message="message must be a non-empty string and selectedToolIds must be an array.",
            ),
            room=sid,
        )
        return
    if not session_id or session_manager.get_session(session_id) is None:
        session = session_manager.create()
        session_id = session["id"]
        await sio.emit("sessionCreated", session, room=sid)

    socket_active_sessions.setdefault(sid, set()).add(session_id)
    terminal_emitted = False
    latest_event: dict[str, Any] = {}

    async def emit(event: str, payload: dict[str, Any]) -> None:
        nonlocal terminal_emitted, latest_event
        latest_event = payload
        if event in {"chat:completed", "chat:cancelled", "chat:error"}:
            terminal_emitted = True
        await sio.emit(event, payload, room=sid)
        if event == "chat:started":
            current = session_manager.get_public_session(session_id)
            if current:
                await sio.emit("sessionUpdated", current)

    try:
        await agent_runtime.run(
            session_id=session_id,
            message=message,
            selected_tool_ids=selected_tool_ids,
            settings=foundry_settings,
            emit=emit,
            request_id=request_id,
        )
        current = session_manager.get_public_session(session_id)
        if current:
            await sio.emit("sessionUpdated", current)
    except AgentRunBusyError as exc:
        await sio.emit(
            "chat:error",
            _chat_error_payload(
                request_id=request_id,
                session_id=session_id,
                code="RunBusy",
                message=str(exc),
            ),
            room=sid,
        )
    except asyncio.CancelledError:
        current = session_manager.get_public_session(session_id)
        if current:
            await sio.emit("sessionUpdated", current)
    except Exception as exc:
        if not terminal_emitted:
            internal = session_manager.get_session(session_id)
            assistant = next(
                (
                    item for item in reversed(internal.get("messages", []))
                    if item.get("role") == "assistant" and item.get("status") == "streaming"
                ),
                None,
            ) if internal else None
            if assistant:
                session_manager.update_message(session_id, assistant["id"], status="error")
                session_manager.rollback_agent_session(session_id)
            current = session_manager.get_public_session(session_id)
            await sio.emit(
                "chat:error",
                _chat_error_payload(
                    request_id=request_id,
                    session_id=session_id,
                    message_id=str(latest_event.get("messageId") or (assistant or {}).get("id") or ""),
                    epoch=int(latest_event.get("epoch") or 0),
                    sequence=int(latest_event.get("sequence") or 0) + 1,
                    code=type(exc).__name__,
                    message="Agent request failed. Check the server log for details.",
                    content=str((assistant or {}).get("content") or ""),
                    session=current,
                ),
                room=sid,
            )
        logger.exception("Agent run failed for session %s", session_id)
        current = session_manager.get_public_session(session_id)
        if current:
            await sio.emit("sessionUpdated", current)
    finally:
        socket_active_sessions.get(sid, set()).discard(session_id)


@sio.on("chat:cancel")
async def chat_cancel(sid: str, data: dict[str, Any]) -> None:
    if not agent_runtime:
        return
    data = data if isinstance(data, dict) else {}
    session_id = str(data.get("sessionId") or "")
    request_id = data.get("requestId")
    cancelled = await agent_runtime.cancel(session_id, str(request_id) if request_id else None)
    if not cancelled:
        await sio.emit(
            "chat:error",
            _chat_error_payload(
                request_id=str(request_id or ""),
                session_id=session_id,
                code="RunNotFound",
                message="No matching active run was found.",
            ),
            room=sid,
        )


@sio.on("chat:approval-resolve")
async def chat_approval_resolve(sid: str, data: dict[str, Any]) -> None:
    if not agent_runtime:
        return
    data = data if isinstance(data, dict) else {}
    request_id = str(data.get("requestId") or "")
    session_id = str(data.get("sessionId") or "")
    try:
        approve_all = data.get("approveAll", False)
        if not isinstance(approve_all, bool):
            raise ValueError("approveAll must be a boolean.")
        decisions = data.get("decisions", [])
        if not isinstance(decisions, list):
            raise ValueError("decisions must be an array.")
        agent_runtime.resolve_approval(
            request_id,
            decisions,
            approve_all=approve_all,
        )
    except ValueError as exc:
        # A malformed/stale decision must not leave the agent awaiting input.
        # Resolve every pending call as denied, then report a non-terminal UI error.
        try:
            agent_runtime.resolve_approval(request_id, [], approve_all=False)
        except ApprovalNotFoundError:
            pass
        await sio.emit(
            "error",
            {"message": f"Invalid approval decision; all pending calls were denied. {exc}"},
            room=sid,
        )
    except ApprovalNotFoundError as exc:
        await sio.emit(
            "chat:error",
            _chat_error_payload(
                request_id=request_id,
                session_id=session_id,
                code=type(exc).__name__,
                message=str(exc),
            ),
            room=sid,
        )


# ---------------------------------------------------------------------------
# MCP registration and lifecycle
# ---------------------------------------------------------------------------


@sio.event
async def registerMCPServer(sid: str, serverConfig: dict[str, Any]) -> None:
    if mcp_manager is None:
        return
    server_config = dict(serverConfig)
    server_config["id"] = server_config.get("id") or server_config.get("name")
    server_id = str(server_config.get("id") or "")
    status_msg: dict[str, Any]
    saved_servers_manager.save_server(server_config)
    try:
        new_tools = await mcp_manager.add_server(server_config)
        status_msg = {"id": server_id, "status": "connected"}
        await sio.emit("mcpServerRegistered", server_config)
        await sio.emit("mcpServerTools", {"serverId": server_id, "tools": new_tools})
    except Exception as exc:
        new_tools = []
        status_msg = {"id": server_id, "status": "error", "error": str(exc)}
        logger.warning("Could not connect MCP server %s: %s", server_id, exc)
    server_statuses[server_id] = status_msg
    await sio.emit("mcpServerStatus", status_msg)
    await sio.emit("mcpServers", mcp_manager.server_configs())
    await sio.emit("savedServers", saved_servers_manager.get_saved())


@sio.event
async def getMCPServers(sid: str) -> None:
    await sio.emit("mcpServers", mcp_manager.server_configs() if mcp_manager else [], room=sid)


@sio.event
async def getMCPServerTools(sid: str, serverId: str) -> None:
    tools = [item for item in mcp_manager.function_defs if item["serverId"] == serverId] if mcp_manager else []
    await sio.emit("mcpServerTools", {"serverId": serverId, "tools": tools}, room=sid)


@sio.event
async def removeMCPServer(sid: str, serverIdentifier: str) -> None:
    if mcp_manager:
        await mcp_manager.remove_server(serverIdentifier)
    await sio.emit("mcpServerRemoved", serverIdentifier)
    await sio.emit("mcpServers", mcp_manager.server_configs() if mcp_manager else [])
    await sio.emit("allToolsUpdated", mcp_manager.function_defs if mcp_manager else [])


@sio.event
async def getSavedServers(sid: str) -> None:
    await sio.emit("savedServers", saved_servers_manager.get_saved(), room=sid)


@sio.event
async def deleteSavedServer(sid: str, serverName: str) -> None:
    saved_servers_manager.delete(serverName)
    await sio.emit("savedServerDeleted", serverName, room=sid)


@sio.event
async def getMCPServerStatus(sid: str, serverKey: str) -> None:
    await sio.emit(
        "mcpServerStatus",
        server_statuses.get(serverKey, {"id": serverKey, "status": "unknown"}),
        room=sid,
    )


@fastapi_app.get("/callback/{server_id}")
async def oauth_callback(server_id: str, request: Request) -> HTMLResponse:
    delivered = register_callback_result(
        server_id,
        request.query_params.get("code"),
        request.query_params.get("state"),
    )
    if not delivered:
        raise HTTPException(status_code=400, detail="No matching OAuth flow is pending.")
    return HTMLResponse(content="Authentication complete. You can close this window.")


# Register the client build last so API routes keep precedence while root-level
# assets such as manifest.json and robots.txt remain available.
if BUILD_DIR.exists():
    fastapi_app.mount(
        "/",
        StaticFiles(directory=str(BUILD_DIR), html=True),
        name="client-build",
    )
