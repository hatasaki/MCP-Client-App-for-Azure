import json
import uuid
import os
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import socketio
from datetime import datetime
import asyncio
from pathlib import Path
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from app.responses_api import AzureOpenAIResponseService  # new import

# ---------- ASGI アプリ構成 ----------
fastapi_app = FastAPI()
fastapi_app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=['*'],
    allow_headers=['*'],
)

# Mount React build static assets under /static (avoid clobbering API routes)
BUILD_DIR = Path(__file__).resolve().parent.parent / "client" / "build"
STATIC_DIR = BUILD_DIR / "static"
if STATIC_DIR.exists():
    fastapi_app.mount("/static", StaticFiles(directory=str(STATIC_DIR), html=False), name="static")

    index_path = BUILD_DIR / "index.html"

    if index_path.exists():
        @fastapi_app.get("/")
        async def serve_root():
            return FileResponse(index_path)

sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")
app = socketio.ASGIApp(sio, other_asgi_app=fastapi_app)   # ← uvicorn が読むのはこの `app`
# -------------------------------------

from app.config import load_or_create_azure_conf, load_mcp_servers, save_azure_conf, save_mcp_servers
from app.mcp_manager import MCPManager, register_callback_result
from app.session_manager import SessionManager
from app.saved_servers_manager import SavedServersManager
from app.azure_openai_service import AzureOpenAIService

# Load configs
azure_conf = load_or_create_azure_conf()
# servers stored in data/mcp.json
mcp_servers = load_mcp_servers()

# Initialize components
mcp_manager: MCPManager | None = None
session_manager = SessionManager()
saved_servers_manager = SavedServersManager()
azure_service: AzureOpenAIService | None = None
connected_servers: list[dict] = []
pending_approvals: dict[str, asyncio.Future] = {}
# maintain a flag per session if user stopped generation early
user_stop_flags: dict[str, bool] = {}
# cache latest status per server key (id or name)
server_statuses: dict[str, dict] = {}

@fastapi_app.on_event("startup")
async def startup_event():
    global mcp_manager, azure_service
    # start with no connected MCP servers – user registers manually
    mcp_manager = await MCPManager([]).__aenter__()
    # init Azure client whenever endpoint is configured. The AzureOpenAIService
    # constructor will internally decide whether to use an API key or Entra ID
    # (AAD) authentication depending on whether ``api_key`` is present.
    if azure_conf.get("endpoint"):
        api_type_cfg = azure_conf.get("api_type", "chat")
        print("API type:", api_type_cfg)
        svc_cls = AzureOpenAIResponseService if api_type_cfg in ("response", "responses") else AzureOpenAIService
        azure_service = svc_cls(azure_conf)
    else:
        azure_service = None
        print("[Startup] Azure OpenAI client not created (missing endpoint)")

@fastapi_app.on_event("shutdown")
async def shutdown_event():
    if mcp_manager:
        await mcp_manager.__aexit__(None, None, None)

@sio.event
async def connect(sid, environ):
    print(f"Client connected: {sid}")
    # send current Azure config on connect
    await sio.emit('azureConfig', azure_conf, room=sid)

@sio.event
async def disconnect(sid):
    print(f"Client disconnected: {sid}")

@sio.event
async def registerMCPServer(sid, serverConfig):
    global mcp_manager
    # persist to saved list via manager (keeps history even if unregistered)
    saved_servers_manager.save_server(serverConfig)
    # reload full list from disk to sync any external edits
    servers = saved_servers_manager.get_saved()
    save_mcp_servers(servers)  # ensure mcp.json up-to-date

    # ensure each server has a unique id (use name as fallback)
    if not serverConfig.get('id'):
        serverConfig['id'] = serverConfig.get('name')

    # prevent duplicates – replace existing entry with same id or name
    connected_servers[:] = [
        s for s in connected_servers
        if s.get('id') not in {serverConfig['id'], None} and s.get('name') != serverConfig.get('name')
    ]

    # connect only this server
    try:
        new_tools = await mcp_manager.add_server(serverConfig)
        status_msg = {'id': serverConfig['id'], 'status': 'connected'}
    except Exception as e:
        # keep entry but flag error
        new_tools = []
        status_msg = {'id': serverConfig['id'], 'status': 'error', 'error': str(e)}
        print(f"❌ Could not connect to {serverConfig.get('name')}: {e}")

    connected_servers.append(serverConfig)

    # Notify client that registration attempt is done
    await sio.emit('mcpServerRegistered', serverConfig)

    # Emit status (success or error)
    await sio.emit('mcpServerStatus', status_msg)

    # If tools discovered successfully, send them
    if new_tools:
        await sio.emit('mcpServerTools', {'serverId': serverConfig.get('name'), 'tools': new_tools})

    # cache status
    server_statuses[serverConfig.get('id') or serverConfig.get('name')] = status_msg
    # Broadcast updated connected list so all clients see the server (even errored)
    await sio.emit('mcpServers', connected_servers)
    await sio.emit('savedServers', servers)

@sio.event
async def getMCPServers(sid):
    await sio.emit('mcpServers', connected_servers, room=sid)

@sio.event
async def getMCPServerTools(sid, serverId):
    tools=[t for t in mcp_manager.function_defs if t.get('serverId')==serverId] if mcp_manager else []
    await sio.emit('mcpServerTools', {'serverId': serverId, 'tools': tools}, room=sid)

@sio.event
async def removeMCPServer(sid, serverName):
    global connected_servers, mcp_manager
    # Determine target name from id or name provided
    target_name = next((s.get('name') for s in connected_servers if s.get('id') == serverName or s.get('name') == serverName), serverName)

    # 1) Remove from currently connected servers (registration)
    connected_servers = [s for s in connected_servers if s.get('name') != target_name]
    # 2) Disconnect & drop tools
    if mcp_manager:
        mcp_manager.remove_server(target_name)
        tools = mcp_manager.function_defs
    else:
        tools = []
    # Notify clients – per-server event
    await sio.emit('mcpServerRemoved', target_name)

    # Broadcast updated connected server list so UI can refresh correctly
    await sio.emit('mcpServers', connected_servers)

    # notify clients that tools of this server are gone
    await sio.emit('mcpServerTools', {'serverId': target_name, 'tools': []})
    # send full remaining tool list for global updates
    await sio.emit('allToolsUpdated', tools)

@sio.event
async def getSavedServers(sid):
    saved = saved_servers_manager.get_saved()
    await sio.emit('savedServers', saved, room=sid)

@sio.event
async def deleteSavedServer(sid, serverName):
    saved_servers_manager.delete(serverName)  # method is synchronous
    await sio.emit('savedServerDeleted', serverName, room=sid)

@sio.event
async def createNewSession(sid):
    session_id = str(uuid.uuid4())
    session = session_manager.create(session_id)
    await sio.emit('sessionCreated', session, room=sid)

@sio.event
async def getSessions(sid):
    sessions = session_manager.list()
    await sio.emit('sessions', sessions, room=sid)

@sio.event
async def loadSession(sid, sessionId):
    session = session_manager.get(sessionId)
    if session:
        await sio.emit('sessionLoaded', session, room=sid)
    else:
        await sio.emit('error', {'message': 'Session not found'}, room=sid)

@sio.event
async def deleteSession(sid, sessionId):
    session_manager.delete(sessionId)
    sessions = session_manager.list()
    await sio.emit('sessions', sessions)

@sio.event
async def sendMessage(sid, data):
    # data: { sessionId, message, selectedTools, azureConfig }
    session_id = data.get('sessionId')
    # Clear stop flag at start of new request
    if session_id:
        user_stop_flags.pop(session_id, None)
    user_message = data.get('message', '')
    selected_tools = data.get('selectedTools', [])
    client_cfg = data.get('azureConfig', {})
    # Detect forced tool invocation syntax (message starts with #<toolName>)
    forced_tool_name = None
    if user_message.startswith('#'):
        # extract token until whitespace or end
        token = user_message[1:].split()[0] if len(user_message) > 1 else ''
        if token:
            sel_names = {t.get('name') if isinstance(t, dict) else t for t in selected_tools}
            if token in sel_names:
                forced_tool_name = token
                # Remove the #toolname prefix from the actual message content sent to the model
                user_message = user_message[len(token)+1:].lstrip()

    # pick azure config
    azure_cfg_used = client_cfg if client_cfg else azure_conf

    # ensure session exists
    session = session_manager.get_session(session_id) if session_id else None
    if not session:
        session = session_manager.create_session()
        session_id = session['id']

    # append user message to session
    now_iso=datetime.utcnow().isoformat()+"Z"
    messages = session.get('messages', []) + [{ 'role': 'user', 'content': user_message, 'timestamp': now_iso }]
    # update & notify immediately so client shows user message without waiting
    session_manager.update_session(session_id, {'messages': messages})
    await sio.emit('sessionUpdated', session_manager.get(session_id), room=sid)

    # prepare tools metadata
    # Only include functions for tools explicitly selected by the user. If the user has not
    # selected any tools, we pass an empty list so that the model cannot call tools
    # automatically.
    if selected_tools:
        sel_names = {t.get('name') if isinstance(t, dict) else t for t in selected_tools}
        tools = [f for f in mcp_manager.function_defs if f['name'] in sel_names]
    else:
        tools = []

    # ensure azure_service is initialized and matches requested api type
    desired_api = (azure_cfg_used.get("api_type") or azure_cfg_used.get("apiType") or "chat").lower()
    print("API config:", azure_cfg_used.get("api_type") , azure_cfg_used.get("apiType"), "API type:", desired_api)

    global azure_service
    def _svc_is_response(svc):
        from app.responses_api import AzureOpenAIResponseService as _R
        return isinstance(svc, _R)
    def _svc_is_chat(svc):
        from app.azure_openai_service import AzureOpenAIService as _C
        return isinstance(svc, _C)
    def _cfg_changed(svc, cfg: dict) -> bool:
        try:
            curr = getattr(svc, 'config', {}) or {}
            # Compare JSON-serialized configs (order-insensitive)
            return json.dumps(curr, sort_keys=True, default=str) != json.dumps(cfg or {}, sort_keys=True, default=str)
        except Exception:
            return True

    if (
        azure_service is None or
        (_svc_is_response(azure_service) and desired_api=="chat") or
        (_svc_is_chat(azure_service) and desired_api in ("response", "responses")) or
        _cfg_changed(azure_service, azure_cfg_used)
    ):
        svc_cls = AzureOpenAIResponseService if desired_api in ("response", "responses") else AzureOpenAIService
        azure_service = svc_cls(azure_cfg_used)

    async def approval_cb(req):
        fid=req['id']=str(uuid.uuid4())
        fut=asyncio.get_event_loop().create_future()
        pending_approvals[fid]=fut
        await sio.emit('approvalRequired', req, room=sid)
        res=await fut
        approved, always = res.get('approved'), res.get('approveAll', False)
        if always and session:
            session['autoApproveAll'] = True
        return approved, always  # always per-call approval

    # define tool executor that notifies client when tool execution starts
    async def exec_tool(session_id_: str, name: str, args: dict):
        # Inform the originating socket that a tool execution has started so that the
        # client UI can render the tool name while waiting for the final answer.
        await sio.emit('toolStarted', {
            'sessionId': session_id_,
            'toolName': name
        }, room=sid)
        # Execute the actual tool via the MCP manager and return its result
        return await mcp_manager.call_tool(name, args)

    # call Azure OpenAI ChatCompletion with tool calls
    try:
        result = await azure_service.send_message(
            session_id=session_id,
            messages=messages,
            tools=tools,
            tool_executor=exec_tool,
            approval_callback=approval_cb,
            auto_approve=session.get('autoApproveAll', False),
            previous_response_id=session.get('responseId'),
            forced_tool_name=forced_tool_name,
            should_stop=lambda sid: user_stop_flags.get(sid, False)
        )
        # If stopped flag set, abort without updating session further
        if result.get('stopped'):
            user_stop_flags.pop(session_id, None)
            return
    except Exception as e:
        # If an error occurs when calling Azure OpenAI, respond with a predefined message in English
        error_reply = "Sorry, your request failed. Please try again."

        now_iso = datetime.utcnow().isoformat() + "Z"
        assistant_error_msg = {
            'role': 'assistant',
            'content': error_reply,
            'timestamp': now_iso,
            'toolCalls': []
        }

        # Update session with the error message from the assistant
        session_manager.update_session(session_id, {
            'messages': messages + [assistant_error_msg]
        })

        # Send error response back to the requesting client
        await sio.emit('messageResponse', {
            'sessionId': session_id,
            'content': error_reply,
            'toolCalls': []
        }, room=sid)

        # Notify all clients about the updated session
        await sio.emit('sessionUpdated', session_manager.get(session_id))

        # Optionally send detailed error information to the client (can be removed in production)
        await sio.emit('error', {'message': str(e)}, room=sid)

        # Log the error server-side for debugging
        print(f"[Azure OpenAI Error] {e}")
        return

    # After receiving `result`, before updating session, honour potential stop flag
    if user_stop_flags.get(session_id):
        # User stopped generation after we started; suppress this response
        user_stop_flags.pop(session_id, None)
        return

    # update session with assistant message
    assistant_content = result.get('content', '')
    tool_calls=result.get('toolCalls',[])
    now_iso=datetime.utcnow().isoformat()+"Z"
    new_messages = messages + [{ 'role': 'assistant', 'content': assistant_content, 'timestamp': now_iso, 'toolCalls': tool_calls }]
    session_manager.update_session(session_id, {
        'messages': new_messages,
        'responseId': result.get('response_id')
    })

    # send response back to client
    await sio.emit('messageResponse', {
        'sessionId': session_id,
        'content': assistant_content,
        'toolCalls': result.get('toolCalls',[])
    }, room=sid)

    # notify all clients about updated session
    updated=session_manager.get(session_id)
    await sio.emit('sessionUpdated', updated)

@sio.event
async def approvalResult(sid, data):
    fid=data.get('id')
    fut=pending_approvals.pop(fid,None)
    if fut and not fut.done():
        fut.set_result(data)

@sio.event
async def stopGeneration(sid, data):
    """Handle user request to stop current AI response generation."""
    session_id = data.get('sessionId')
    if not session_id:
        return
    session = session_manager.get(session_id)
    if session is None:
        return

    # Mark the session so that any in-flight response will be ignored
    user_stop_flags[session_id] = True

    # Append an assistant message indicating the stop
    now_iso = datetime.utcnow().isoformat() + "Z"
    assistant_msg = {
        'role': 'assistant',
        'content': 'stopped by user',
        'timestamp': now_iso,
        'toolCalls': []
    }
    messages = session.get('messages', []) + [assistant_msg]
    session_manager.update_session(session_id, {'messages': messages})

    # Notify the requesting client immediately
    await sio.emit('messageResponse', {
        'sessionId': session_id,
        'content': 'stopped by user',
        'toolCalls': []
    }, room=sid)
    # Broadcast session update to all clients
    await sio.emit('sessionUpdated', session_manager.get(session_id))

@fastapi_app.get("/azure-config-status")
async def azure_config_status():
    """Return whether server-side Azure config is effectively configured (endpoint & deployment)."""
    ok = bool(azure_conf.get("endpoint") and azure_conf.get("deployment"))
    return {"isConfigured": ok}

@fastapi_app.get("/azure-config")
async def get_azure_config():
    if not azure_conf:
        raise HTTPException(status_code=404, detail="Azure config not set")
    return azure_conf

@fastapi_app.post("/azure-config")
async def set_azure_config(cfg: dict):
    global azure_conf, azure_service
    # basic validation
    if not cfg.get("endpoint") or not cfg.get("deployment"):
        raise HTTPException(status_code=400, detail="endpoint and deployment are required")
    # Accept and persist new Responses API params as-is (empty string means omit)
    azure_conf.update(cfg)
    save_azure_conf(azure_conf)
    # recreate client
    svc_cls = AzureOpenAIResponseService if azure_conf.get("api_type") in ("response", "responses") else AzureOpenAIService
    azure_service = svc_cls(azure_conf)
    await sio.emit('azureConfig', azure_conf)
    return azure_conf

@sio.event
async def getMCPServerStatus(sid, serverKey):
    """Return cached status for requested MCP server."""
    msg = server_statuses.get(serverKey)
    if msg is None:
        # Unknown server key – respond with unknown status
        msg = {"id": serverKey, "status": "unknown"}
    await sio.emit('mcpServerStatus', msg, room=sid)

# OAuth callback endpoint for client
@fastapi_app.get("/callback")
async def oauth_callback(request: Request):
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    register_callback_result(code, state)
    return HTMLResponse(content="Authentication complete. You can close this window.")
