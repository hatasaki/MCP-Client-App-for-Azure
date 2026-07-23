from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import socketio
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from app.agent_runtime import (
    AgentRunBusyError,
    AgentRuntime,
    ApprovalNotFoundError,
)
from app.attachments import (
    AttachmentError,
    SOCKET_MAX_HTTP_BUFFER_BYTES,
    parse_incoming_attachments,
)
from app.config import (
    SKILLS_PATH,
    foundry_settings_store,
    load_foundry_settings,
    update_foundry_settings,
)
from app.foundry_config import FoundrySettings, FoundrySettingsWrite, ModelSelection, SCHEMA_VERSION
from app.mcp_manager import MCPManager, register_callback_result
from app.saved_servers_manager import SavedServersManager
from app.session_manager import SessionManager
from app.secret_protection import SecretProtectionError
from app.skills_manager import MAX_SKILL_UPLOAD_BYTES, SkillLibraryError, SkillsManager

logger = logging.getLogger("mcpclient.backend")

session_manager = SessionManager()
saved_servers_manager = SavedServersManager()
mcp_manager: MCPManager | None = None
agent_runtime: AgentRuntime | None = None
skills_manager: SkillsManager | None = None
foundry_settings: FoundrySettings | None = None
foundry_settings_error: str | None = None
recoverable_foundry_settings: FoundrySettings | None = None
server_statuses: dict[str, dict[str, Any]] = {}
socket_active_sessions: dict[str, dict[str, str]] = {}
socket_generations: dict[str, str] = {}
socket_registry_lock = asyncio.Lock()
settings_operation_lock = asyncio.Lock()


@asynccontextmanager
async def app_lifespan(_app: FastAPI):
    global mcp_manager, agent_runtime, skills_manager, foundry_settings, foundry_settings_error, recoverable_foundry_settings
    try:
        foundry_settings = load_foundry_settings()
        foundry_settings_error = None
        recoverable_foundry_settings = None
    except (SecretProtectionError, ValidationError, ValueError, TypeError, OSError, json.JSONDecodeError) as exc:
        foundry_settings = None
        try:
            recoverable_foundry_settings = foundry_settings_store.load_recoverable_settings()
        except (SecretProtectionError, ValidationError, ValueError, TypeError, OSError, json.JSONDecodeError):
            recoverable_foundry_settings = None
        foundry_settings_error = (
            str(exc) if isinstance(exc, SecretProtectionError)
            else "Microsoft Foundry settings are invalid or corrupted. Replace the complete settings."
        )
        logger.error("Microsoft Foundry settings could not be loaded: %s", exc)
    if foundry_settings:
        session_manager.reconcile_model_selections(foundry_settings)
    skills_manager = SkillsManager(SKILLS_PATH)
    await skills_manager.validate_library()
    session_manager.reconcile_skill_selections(skills_manager.ids())
    mcp_manager = await MCPManager([]).__aenter__()
    agent_runtime = AgentRuntime(session_manager, mcp_manager, skills_manager=skills_manager)
    try:
        yield
    finally:
        if agent_runtime:
            await agent_runtime.shutdown()
        if mcp_manager:
            await mcp_manager.close()


fastapi_app = FastAPI(title="MCP Client for Microsoft Foundry", lifespan=app_lifespan)


@fastapi_app.exception_handler(RequestValidationError)
async def request_validation_error_handler(
    _request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """Return validation details without echoing secret-bearing request input."""
    return JSONResponse(
        status_code=422,
        content={
            "detail": [
                {
                    key: value
                    for key, value in error.items()
                    if key not in {"ctx", "input", "url"}
                }
                for error in exc.errors()
            ]
        },
    )
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
    max_http_buffer_size=SOCKET_MAX_HTTP_BUFFER_BYTES,
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
    async with socket_registry_lock:
        socket_generations[sid] = str(uuid.uuid4())
        socket_active_sessions[sid] = {}
    logger.info("Client connected: %s", sid)


@sio.event
async def disconnect(sid: str) -> None:
    async with socket_registry_lock:
        socket_generations.pop(sid, None)
        sessions = socket_active_sessions.pop(sid, {})
    if agent_runtime:
        await asyncio.gather(
            *(
                agent_runtime.cancel_and_wait(session_id, request_id)
                for session_id, request_id in sessions.items()
            ),
            return_exceptions=True,
        )
    logger.info("Client disconnected: %s", sid)


# ---------------------------------------------------------------------------
# Foundry settings REST API
# ---------------------------------------------------------------------------


@fastapi_app.get("/foundry-settings/status")
async def foundry_settings_status() -> dict[str, Any]:
    status = {
        "isConfigured": foundry_settings is not None,
        "schemaVersion": foundry_settings.schema_version if foundry_settings else SCHEMA_VERSION,
    }
    if foundry_settings_error:
        status["error"] = foundry_settings_error
    if recoverable_foundry_settings:
        recoverable = recoverable_foundry_settings.public_dict()
        recoverable["auth"]["apiKeyConfigured"] = False
        recoverable["auth"]["apiKeyNeedsReplacement"] = True
        status["recoverableSettings"] = recoverable
    return status


@fastapi_app.get("/foundry-settings")
async def get_foundry_settings() -> dict[str, Any]:
    if foundry_settings is None:
        if foundry_settings_error:
            raise HTTPException(status_code=503, detail=foundry_settings_error)
        raise HTTPException(status_code=404, detail="Microsoft Foundry settings are not configured.")
    return foundry_settings.public_dict()


@fastapi_app.put("/foundry-settings")
async def set_foundry_settings(payload: FoundrySettingsWrite) -> dict[str, Any]:
    global foundry_settings, foundry_settings_error, recoverable_foundry_settings
    try:
        async with settings_operation_lock:
            try:
                existing = foundry_settings or foundry_settings_store.load()
            except SecretProtectionError:
                existing = foundry_settings_store.load_recoverable_settings()
            except (ValidationError, ValueError, TypeError, OSError, json.JSONDecodeError):
                existing = None
            candidate = payload.resolve(existing)
            if agent_runtime:
                foundry_settings, reconciled = await agent_runtime.apply_settings_update(
                    candidate,
                    lambda: update_foundry_settings(payload),
                )
            else:
                foundry_settings = update_foundry_settings(payload)
                reconciled = session_manager.reconcile_model_selections(foundry_settings)
    except AgentRunBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.errors(
                include_url=False,
                include_context=False,
                include_input=False,
            ),
        ) from exc
    except SecretProtectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    foundry_settings_error = None
    recoverable_foundry_settings = None
    public = foundry_settings.public_dict()
    await sio.emit("foundrySettingsUpdated", public)
    for session in reconciled:
        await sio.emit("sessionUpdated", session)
    return public


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


@fastapi_app.get("/skills")
async def get_skills() -> dict[str, Any]:
    return {"skills": skills_manager.list() if skills_manager else []}


async def _read_limited_body(request: Request, limit: int) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise HTTPException(status_code=413, detail="The skill upload exceeds the 10 MB limit.")
    return bytes(body)


@fastapi_app.post("/skills/upload")
async def upload_skills(request: Request) -> dict[str, Any]:
    if skills_manager is None:
        raise HTTPException(status_code=503, detail="Skills manager is not available.")
    filename = unquote(request.headers.get("x-skill-filename", "").strip())
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_SKILL_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail="The skill upload exceeds the 10 MB limit.")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid Content-Length header.") from exc
    body = await _read_limited_body(request, MAX_SKILL_UPLOAD_BYTES)
    try:
        async with settings_operation_lock:
            if agent_runtime and agent_runtime.has_active_runs():
                raise HTTPException(status_code=409, detail="Skills cannot be changed while a chat run is active.")
            uploaded = await skills_manager.upload(filename, body)
    except SkillLibraryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await sio.emit("skillsUpdated", {"skills": skills_manager.list()})
    return {"uploaded": uploaded, "skills": skills_manager.list()}


@fastapi_app.delete("/skills/{skill_id}")
async def delete_skill(skill_id: str) -> dict[str, Any]:
    if skills_manager is None:
        raise HTTPException(status_code=503, detail="Skills manager is not available.")
    try:
        async with settings_operation_lock:
            if agent_runtime and agent_runtime.has_active_runs():
                raise HTTPException(status_code=409, detail="Skills cannot be changed while a chat run is active.")
            await skills_manager.delete(skill_id)
            reconciled = session_manager.remove_skill_from_sessions(skill_id)
    except SkillLibraryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    payload = {"skills": skills_manager.list()}
    await sio.emit("skillsUpdated", payload)
    for session in reconciled:
        await sio.emit("sessionUpdated", session)
    return payload


@sio.event
async def createNewSession(sid: str) -> None:
    default_selection = foundry_settings.default_selection if foundry_settings else None
    session = session_manager.create(str(uuid.uuid4()), default_selection)
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
    connection_generation = socket_generations.get(sid)
    if connection_generation is None:
        return
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
    try:
        attachments = parse_incoming_attachments(data.get("attachments", []))
    except AttachmentError as exc:
        await sio.emit(
            "chat:error",
            _chat_error_payload(
                request_id=request_id,
                session_id=session_id,
                code="InvalidAttachment",
                message=str(exc),
            ),
            room=sid,
        )
        return
    selected_tool_ids = data.get("selectedToolIds", [])
    selected_model = data.get("selectedModel")
    selected_skill_ids_value = data.get("selectedSkillIds")
    if (
        not isinstance(message, str)
        or (not message.strip() and not attachments)
        or not isinstance(selected_tool_ids, list)
        or any(not isinstance(item, str) for item in selected_tool_ids)
        or (
            selected_skill_ids_value is not None
            and (
                not isinstance(selected_skill_ids_value, list)
                or any(not isinstance(item, str) for item in selected_skill_ids_value)
            )
        )
        or (selected_model is not None and not isinstance(selected_model, dict))
    ):
        await sio.emit(
            "chat:error",
            _chat_error_payload(
                request_id=request_id,
                session_id=session_id,
                code="InvalidRequest",
                message=(
                    "message must be a string and either message or attachments must be non-empty; "
                    "selectedToolIds and selectedSkillIds must be arrays, "
                    "and selectedModel must be an object when provided."
                ),
            ),
            room=sid,
        )
        return
    reservation = None
    created_session: dict[str, Any] | None = None
    ownership_registered = False
    disconnected = False
    selected_skill_ids: list[str] = []

    async def unregister_socket_run() -> None:
        nonlocal ownership_registered
        if not ownership_registered:
            return
        async with socket_registry_lock:
            active = socket_active_sessions.get(sid)
            if (
                socket_generations.get(sid) == connection_generation
                and active
                and active.get(session_id) == request_id
            ):
                active.pop(session_id, None)
        ownership_registered = False

    try:
        async with settings_operation_lock:
            active_settings = foundry_settings
            if active_settings is None:
                raise ValueError("Microsoft Foundry settings are not configured.")
            existing_session = session_manager.get_session(session_id) if session_id else None
            if existing_session is not None:
                reservation = await agent_runtime.reserve_run(session_id, request_id)
                selected_skill_ids = (
                    list(selected_skill_ids_value)
                    if isinstance(selected_skill_ids_value, list)
                    else list(existing_session.get("selectedSkillIds", []))
                )
                selection_value = selected_model if selected_model is not None else (
                    existing_session.get("selectedModel") or active_settings.default_selection
                )
                try:
                    selection = ModelSelection.model_validate(selection_value)
                    runtime_settings = active_settings.resolve(selection)
                    if skills_manager:
                        skills_manager.fingerprint(selected_skill_ids)
                    elif selected_skill_ids:
                        raise ValueError("Agent Skills are not available.")
                except (ValidationError, ValueError, KeyError):
                    await agent_runtime.release_run(reservation)
                    reservation = None
                    raise
            else:
                selected_skill_ids = list(selected_skill_ids_value) if isinstance(selected_skill_ids_value, list) else []
                selection_value = selected_model if selected_model is not None else active_settings.default_selection
                selection = ModelSelection.model_validate(selection_value)
                runtime_settings = active_settings.resolve(selection)
                if skills_manager:
                    skills_manager.fingerprint(selected_skill_ids)
                elif selected_skill_ids:
                    raise ValueError("Agent Skills are not available.")
                session_id = str(uuid.uuid4())
                reservation = await agent_runtime.reserve_run(session_id, request_id)
            async with socket_registry_lock:
                if socket_generations.get(sid) != connection_generation:
                    disconnected = True
                else:
                    socket_active_sessions[sid][session_id] = request_id
                    ownership_registered = True
            if disconnected:
                await agent_runtime.release_run(reservation)
                reservation = None
            elif existing_session is None:
                created_session = session_manager.create(session_id, selection)
                if selected_skill_ids:
                    created_session = session_manager.set_selected_skills(session_id, selected_skill_ids)
    except AgentRunBusyError as exc:
        current = session_manager.get_public_session(session_id)
        await sio.emit(
            "chat:error",
            _chat_error_payload(
                request_id=request_id,
                session_id=session_id,
                code="RunBusy",
                message=str(exc),
                session=current,
            ),
            room=sid,
        )
        return
    except SkillLibraryError as exc:
        await sio.emit(
            "chat:error",
            _chat_error_payload(
                request_id=request_id,
                session_id=session_id,
                code="InvalidSkillSelection",
                message=str(exc),
            ),
            room=sid,
        )
        return
    except (ValidationError, ValueError, KeyError) as exc:
        await sio.emit(
            "chat:error",
            _chat_error_payload(
                request_id=request_id,
                session_id=session_id,
                code="InvalidModelSelection",
                message=str(exc),
            ),
            room=sid,
        )
        return
    except BaseException:
        await unregister_socket_run()
        if reservation is not None:
            await agent_runtime.release_run(reservation)
        raise
    if disconnected:
        return
    if reservation is None:
        raise RuntimeError("Agent run reservation was not created.")

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
        if created_session is not None:
            await sio.emit("sessionCreated", created_session, room=sid)
        await agent_runtime.run(
            session_id=session_id,
            message=message,
            attachments=attachments,
            selected_skill_ids=selected_skill_ids,
            selected_tool_ids=selected_tool_ids,
            settings=runtime_settings,
            emit=emit,
            request_id=request_id,
            reservation=reservation,
        )
        current = session_manager.get_public_session(session_id)
        if current:
            await sio.emit("sessionUpdated", current)
    except AgentRunBusyError as exc:
        current = session_manager.get_public_session(session_id)
        await sio.emit(
            "chat:error",
            _chat_error_payload(
                request_id=request_id,
                session_id=session_id,
                code="RunBusy",
                message=str(exc),
                session=current,
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
        await agent_runtime.release_run(reservation)
        await unregister_socket_run()


@sio.event
async def setSessionModel(sid: str, data: dict[str, Any]) -> None:
    data = data if isinstance(data, dict) else {}
    session_id = str(data.get("sessionId") or "")
    selection_value = data.get("selectedModel")
    if foundry_settings is None:
        await sio.emit("error", {"message": "Microsoft Foundry settings are not configured."}, room=sid)
        return
    if session_manager.get_session(session_id) is None:
        await sio.emit("error", {"message": "Session not found."}, room=sid)
        return
    try:
        selection = ModelSelection.model_validate(selection_value)
        foundry_settings.resolve(selection)
    except (ValidationError, ValueError, KeyError) as exc:
        await sio.emit("error", {"message": str(exc)}, room=sid)
        return
    try:
        updated = (
            await agent_runtime.set_selected_model(session_id, selection)
            if agent_runtime
            else session_manager.set_selected_model(session_id, selection)
        )
    except AgentRunBusyError:
        await sio.emit("error", {"message": "The model cannot be changed while a run is active."}, room=sid)
        current = session_manager.get_public_session(session_id)
        if current:
            await sio.emit("sessionUpdated", current, room=sid)
        return
    await sio.emit("sessionUpdated", updated)


@sio.event
async def setSessionSkills(sid: str, data: dict[str, Any]) -> None:
    data = data if isinstance(data, dict) else {}
    session_id = str(data.get("sessionId") or "")
    skill_ids = data.get("selectedSkillIds", [])
    if session_manager.get_session(session_id) is None:
        await sio.emit("error", {"message": "Session not found."}, room=sid)
        return
    if not isinstance(skill_ids, list) or any(not isinstance(item, str) for item in skill_ids):
        await sio.emit("error", {"message": "selectedSkillIds must be an array of strings."}, room=sid)
        return
    try:
        async with settings_operation_lock:
            if skills_manager:
                skills_manager.fingerprint(skill_ids)
            elif skill_ids:
                raise SkillLibraryError("Agent Skills are not available.")
            updated = (
                await agent_runtime.set_selected_skills(session_id, skill_ids)
                if agent_runtime
                else session_manager.set_selected_skills(session_id, skill_ids)
            )
    except (AgentRunBusyError, SkillLibraryError, ValueError) as exc:
        await sio.emit("error", {"message": str(exc)}, room=sid)
        current = session_manager.get_public_session(session_id)
        if current:
            await sio.emit("sessionUpdated", current, room=sid)
        return
    await sio.emit("sessionUpdated", updated)


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
