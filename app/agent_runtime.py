from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from agent_framework import Agent, AgentSession, Content, Message

from app.attachments import AttachmentData, DEFAULT_ATTACHMENT_PROMPT, build_attachment_contents
from app.foundry_config import FoundrySettings, ModelSelection, ResolvedFoundrySettings
from app.mcp_manager import MCPManager
from app.provider_factory import ProviderFactory
from app.session_manager import SessionManager
from app.skills_manager import SkillsManager

EventSink = Callable[[str, dict[str, Any]], Awaitable[None]]


class AgentRuntimeError(RuntimeError):
    pass


class AgentRunBusyError(AgentRuntimeError):
    pass


class ApprovalNotFoundError(AgentRuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    request_id: str
    approved: bool


@dataclass(slots=True)
class PendingApproval:
    run_request_id: str
    session_id: str
    requests: list[Content]
    future: asyncio.Future[tuple[dict[str, bool], bool]]


@dataclass(frozen=True, slots=True)
class RunResult:
    request_id: str
    session_id: str
    message_id: str
    content: str
    status: str
    tool_calls: tuple[str, ...]


@dataclass(slots=True)
class RunReservation:
    session_id: str
    request_id: str
    task: asyncio.Task[Any]
    lock: asyncio.Lock
    released: bool = False


class AgentRuntime:
    """Coordinates MAF Agent streaming, approvals, sessions, and cancellation."""

    def __init__(
        self,
        session_manager: SessionManager,
        mcp_manager: MCPManager,
        *,
        provider_factory: ProviderFactory | None = None,
        skills_manager: SkillsManager | None = None,
    ) -> None:
        self.session_manager = session_manager
        self.mcp_manager = mcp_manager
        self.provider_factory = provider_factory or ProviderFactory()
        self.skills_manager = skills_manager
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._active_tasks: dict[str, asyncio.Task[Any]] = {}
        self._active_request_ids: dict[str, str] = {}
        self._pending_approvals: dict[str, PendingApproval] = {}
        self._registry_lock = asyncio.Lock()

    async def run(
        self,
        *,
        session_id: str,
        message: str,
        attachments: Sequence[AttachmentData] = (),
        selected_skill_ids: Sequence[str] = (),
        selected_tool_ids: Sequence[str],
        settings: FoundrySettings | ResolvedFoundrySettings,
        emit: EventSink,
        request_id: str | None = None,
        reservation: RunReservation | None = None,
    ) -> RunResult:
        if not message.strip() and not attachments:
            raise ValueError("Message or attachment must be provided.")
        run_request_id = request_id or str(uuid4())
        task = asyncio.current_task()
        if task is None:
            raise AgentRuntimeError("Agent runtime requires an asyncio task.")
        lease = reservation or await self.reserve_run(session_id, run_request_id)
        if (
            lease.session_id != session_id
            or lease.request_id != run_request_id
            or lease.task is not task
            or lease.released
        ):
            if not lease.released:
                await self.release_run(lease)
            raise AgentRuntimeError("The run reservation does not match this request.")
        try:
            snapshot = settings.resolve() if isinstance(settings, FoundrySettings) else settings
            normalized_message = message.strip() or DEFAULT_ATTACHMENT_PROMPT
            return await self._run_locked(
                session_id=session_id,
                message=normalized_message,
                attachments=attachments,
                selected_skill_ids=selected_skill_ids,
                selected_tool_ids=selected_tool_ids,
                settings=snapshot,
                emit=emit,
                request_id=run_request_id,
            )
        finally:
            await self.release_run(lease)

    async def reserve_run(self, session_id: str, request_id: str) -> RunReservation:
        """Atomically mark a session active before provider settings are released."""
        task = asyncio.current_task()
        if task is None:
            raise AgentRuntimeError("Agent runtime requires an asyncio task.")
        lock = self._session_locks.setdefault(session_id, asyncio.Lock())
        if lock.locked():
            raise AgentRunBusyError(f"Session '{session_id}' already has an active run.")
        await lock.acquire()
        try:
            async with self._registry_lock:
                self._active_tasks[session_id] = task
                self._active_request_ids[session_id] = request_id
            return RunReservation(session_id, request_id, task, lock)
        except BaseException:
            lock.release()
            raise

    async def release_run(self, reservation: RunReservation) -> None:
        if reservation.released:
            return
        reservation.released = True
        self._cancel_pending_approval(reservation.request_id)
        async with self._registry_lock:
            if self._active_tasks.get(reservation.session_id) is reservation.task:
                self._active_tasks.pop(reservation.session_id, None)
                self._active_request_ids.pop(reservation.session_id, None)
        if reservation.lock.locked():
            reservation.lock.release()

    async def _run_locked(
        self,
        *,
        session_id: str,
        message: str,
        attachments: Sequence[AttachmentData],
        selected_skill_ids: Sequence[str],
        selected_tool_ids: Sequence[str],
        settings: ResolvedFoundrySettings,
        emit: EventSink,
        request_id: str,
    ) -> RunResult:
        # Validate user-controlled tool IDs before mutating persisted session state.
        selected_functions = self.mcp_manager.get_functions(selected_tool_ids)
        forced_qualified_id, clean_message = self._extract_forced_tool(message, selected_tool_ids)
        current_attachment_contents = build_attachment_contents(attachments, settings.api_type)
        skills_provider = self.skills_manager.create_provider(selected_skill_ids) if self.skills_manager else None
        options = settings.to_maf_options()
        configured_tool_choice = options.get("tool_choice")
        if forced_qualified_id is not None:
            forced_function = self.mcp_manager.get_function(forced_qualified_id)
            options["tool_choice"] = {
                "mode": "required",
                "required_function_name": forced_function.name,
            }
        assistant_message_id = str(uuid4())
        epoch = 0
        sequence = 0
        content_parts: list[str] = []
        executed_tools: list[str] = []
        tool_calls_by_call_id: dict[str, str] = {}
        session_prepared = False
        user_message_id: str | None = None
        assistant_persisted = False
        committed = False

        async def send(event: str, payload: Mapping[str, Any] | None = None) -> None:
            nonlocal sequence
            sequence += 1
            body = {
                "requestId": request_id,
                "sessionId": session_id,
                "messageId": assistant_message_id,
                "epoch": epoch,
                "sequence": sequence,
            }
            if payload:
                body.update(payload)
            await emit(event, body)

        try:
            if self.session_manager.selected_model(session_id) != settings.selection:
                self.session_manager.set_selected_model(session_id, settings.selection)
            if self.session_manager.selected_skill_ids(session_id) != list(selected_skill_ids):
                self.session_manager.set_selected_skills(session_id, selected_skill_ids)

            skills_fingerprint = (
                self.skills_manager.fingerprint(selected_skill_ids)
                if self.skills_manager
                else hashlib.sha256(b"[]").hexdigest()
            )
            fingerprint = hashlib.sha256(
                f"{settings.fingerprint()}:{skills_fingerprint}".encode("utf-8")
            ).hexdigest()
            agent_session, replay_required, state_reset = self.session_manager.prepare_agent_session(
                session_id,
                fingerprint,
            )
            session_prepared = True
            attachment_records = self.session_manager.store_attachments(session_id, attachments)
            user_message = self.session_manager.append_message(
                session_id,
                role="user",
                content=message,
                status="completed",
                attachments=attachment_records,
            )
            user_message_id = user_message["id"]
            self.session_manager.append_message(
                session_id,
                role="assistant",
                content="",
                status="streaming",
                message_id=assistant_message_id,
            )
            assistant_persisted = True
            internal_session = self.session_manager.get_session(session_id)
            epoch = int(internal_session.get("stateEpoch", 0)) if internal_session else 0

            await send("chat:started", {
                "userMessageId": user_message["id"],
                "stateReset": state_reset,
                "modelSelection": settings.selection.model_dump(mode="json", by_alias=True),
            })

            current_message = Message(
                role="user",
                contents=[
                    *current_attachment_contents,
                    clean_message,
                ],
            )
            replay = (
                self._build_replay_messages_with_attachments(
                    session_id,
                    settings,
                    exclude_message_id=user_message_id,
                )
                if replay_required
                else []
            )
            current_input: str | list[Message]
            current_input = [*replay, current_message] if replay else current_message
            bundle = self.provider_factory.create(settings)
            agent = Agent(
                client=bundle.client,
                name="MCPClientAgent",
                instructions=settings.agent_instructions,
                tools=selected_functions,
                context_providers=[skills_provider] if skills_provider else None,
                default_options=options,
            )
            async with bundle, agent:
                while True:
                    requests_by_id: dict[str, Content] = {}
                    stream = agent.run(current_input, stream=True, session=agent_session)
                    async for update in stream:
                        if update.text:
                            content_parts.append(update.text)
                            await send("chat:delta", {"delta": update.text})
                        for content in update.contents:
                            await self._handle_tool_content(
                                content,
                                executed_tools,
                                tool_calls_by_call_id,
                                send,
                            )
                        if update.user_input_requests:
                            for request in update.user_input_requests:
                                requests_by_id.setdefault(request.id or str(id(request)), request)
                    # Ensure MAF result hooks run and session state/conversation id are finalized.
                    await stream.get_final_response()
                    requests = list(requests_by_id.values())
                    if not requests:
                        break
                    decisions, approve_all = await self._request_approval(
                        run_request_id=request_id,
                        session_id=session_id,
                        requests=requests,
                        auto_approve=bool(internal_session and internal_session.get("autoApproveAll")),
                        send=send,
                    )
                    if approve_all and internal_session is not None:
                        self.session_manager.update_session(session_id, {"autoApproveAll": True})
                        internal_session = self.session_manager.get_session(session_id)
                    responses = [
                        request.to_function_approval_response(
                            approved=decisions.get(request.id or "", False)
                        )
                        for request in requests
                    ]
                    current_input = [Message(role="user", contents=responses)]
                    # A forced tool applies to the first model call only.
                    if forced_qualified_id is not None:
                        if configured_tool_choice is None:
                            agent.default_options.pop("tool_choice", None)
                        else:
                            agent.default_options["tool_choice"] = configured_tool_choice
                        forced_qualified_id = None

            final_content = "".join(content_parts)
            self.session_manager.update_message(
                session_id,
                assistant_message_id,
                content=final_content,
                status="completed",
                toolCalls=executed_tools,
            )
            self.session_manager.save_agent_session(session_id, agent_session, fingerprint)
            committed = True
            await send("chat:completed", {
                "content": final_content,
                "toolCalls": executed_tools,
                "session": self.session_manager.get_public_session(session_id),
            })
            return RunResult(
                request_id=request_id,
                session_id=session_id,
                message_id=assistant_message_id,
                content=final_content,
                status="completed",
                tool_calls=tuple(executed_tools),
            )
        except asyncio.CancelledError:
            if committed:
                raise
            partial = "".join(content_parts)
            if assistant_persisted:
                self.session_manager.update_message(
                    session_id,
                    assistant_message_id,
                    content=partial,
                    status="cancelled",
                    toolCalls=executed_tools,
                )
            if session_prepared:
                self.session_manager.rollback_agent_session(session_id)
            if assistant_persisted:
                await asyncio.shield(send("chat:cancelled", {
                    "content": partial,
                    "toolCalls": executed_tools,
                    "session": self.session_manager.get_public_session(session_id),
                }))
            raise
        except Exception as exc:
            if committed:
                raise
            partial = "".join(content_parts)
            if assistant_persisted:
                self.session_manager.update_message(
                    session_id,
                    assistant_message_id,
                    content=partial,
                    status="error",
                    toolCalls=executed_tools,
                )
            if session_prepared:
                self.session_manager.rollback_agent_session(session_id)
            if assistant_persisted:
                await send("chat:error", {
                    "code": type(exc).__name__,
                    "message": self._safe_error_message(exc, settings.api_key),
                    "content": partial,
                    "session": self.session_manager.get_public_session(session_id),
                })
            raise

    async def _handle_tool_content(
        self,
        content: Content,
        executed_tools: list[str],
        tool_calls_by_call_id: dict[str, str],
        send: Callable[[str, Mapping[str, Any] | None], Awaitable[None]],
    ) -> None:
        if content.type == "function_call":
            model_name = content.name or ""
            definition = self.mcp_manager.tool_definition_for_model_name(model_name)
            qualified_id = definition["qualifiedId"] if definition else model_name
            if content.call_id:
                tool_calls_by_call_id[content.call_id] = qualified_id
            await send("chat:tool-status", {
                "toolId": qualified_id,
                "toolName": definition["displayName"] if definition else model_name,
                "callId": content.call_id,
                "status": "requested",
                "arguments": self._safe_json(content.arguments),
            })
        elif content.type == "function_result":
            # Function result content carries only call_id. The qualified tool is
            # captured from the preceding function call status; report completion by call.
            qualified_id = tool_calls_by_call_id.get(content.call_id or "")
            if qualified_id and qualified_id not in executed_tools and content.exception is None:
                executed_tools.append(qualified_id)
            await send("chat:tool-status", {
                "toolId": qualified_id,
                "callId": content.call_id,
                "status": "completed" if content.exception is None else "error",
                "error": str(content.exception) if content.exception else None,
            })
        elif content.type == "mcp_server_tool_call":
            model_name = content.tool_name or ""
            definition = self.mcp_manager.tool_definition_for_model_name(model_name)
            qualified_id = definition["qualifiedId"] if definition else model_name
            if qualified_id and qualified_id not in executed_tools:
                executed_tools.append(qualified_id)
        elif content.type == "mcp_server_tool_result" and content.call_id:
            await send("chat:tool-status", {"callId": content.call_id, "status": "completed"})

    async def _request_approval(
        self,
        *,
        run_request_id: str,
        session_id: str,
        requests: list[Content],
        auto_approve: bool,
        send: Callable[[str, Mapping[str, Any] | None], Awaitable[None]],
    ) -> tuple[dict[str, bool], bool]:
        for request in requests:
            if not request.id:
                request.id = str(uuid4())
        if auto_approve:
            return {request.id: True for request in requests if request.id}, True
        loop = asyncio.get_running_loop()
        future: asyncio.Future[tuple[dict[str, bool], bool]] = loop.create_future()
        pending = PendingApproval(run_request_id, session_id, requests, future)
        self._pending_approvals[run_request_id] = pending
        await send("chat:approval-required", {
            "requests": [self._approval_payload(request) for request in requests],
        })
        try:
            return await future
        finally:
            if self._pending_approvals.get(run_request_id) is pending:
                self._pending_approvals.pop(run_request_id, None)

    def resolve_approval(
        self,
        run_request_id: str,
        decisions: Sequence[Mapping[str, Any]],
        *,
        approve_all: bool = False,
    ) -> None:
        pending = self._pending_approvals.get(run_request_id)
        if pending is None or pending.future.done():
            raise ApprovalNotFoundError(f"No pending approval for request '{run_request_id}'.")
        allowed_ids = {request.id or "" for request in pending.requests}
        resolved: dict[str, bool] = {}
        for item in decisions:
            if not isinstance(item, Mapping):
                raise ValueError("Every approval decision must be an object.")
            request_id = item.get("requestId")
            approved = item.get("approved")
            if not isinstance(request_id, str) or not request_id:
                raise ValueError("Every approval decision requires a non-empty requestId.")
            if not isinstance(approved, bool):
                raise ValueError(f"Approval decision '{request_id}' requires a boolean approved value.")
            if request_id in resolved:
                raise ValueError(f"Duplicate approval decision for request '{request_id}'.")
            resolved[request_id] = approved
        unknown = set(resolved) - allowed_ids
        if unknown:
            raise ValueError(f"Unknown approval request ids: {sorted(unknown)}")
        for request_id in allowed_ids:
            resolved.setdefault(request_id, False)
        pending.future.set_result((resolved, approve_all))

    async def cancel(self, session_id: str, request_id: str | None = None) -> bool:
        task = self._active_tasks.get(session_id)
        active_request_id = self._active_request_ids.get(session_id)
        if task is None or task.done():
            return False
        if request_id is not None and request_id != active_request_id:
            return False
        if active_request_id:
            self._cancel_pending_approval(active_request_id)
        task.cancel()
        return True

    async def cancel_and_wait(self, session_id: str, request_id: str | None = None) -> bool:
        task = self._active_tasks.get(session_id)
        if not await self.cancel(session_id, request_id):
            return False
        if task is not None and task is not asyncio.current_task():
            await asyncio.gather(task, return_exceptions=True)
        return True

    def is_active(self, session_id: str) -> bool:
        task = self._active_tasks.get(session_id)
        return bool(task and not task.done())

    def has_active_runs(self) -> bool:
        return any(not task.done() for task in self._active_tasks.values())

    async def set_selected_model(
        self,
        session_id: str,
        selection: ModelSelection,
    ) -> dict[str, Any]:
        """Persist a model selection only when the session has no active run."""
        lock = self._session_locks.setdefault(session_id, asyncio.Lock())
        if lock.locked():
            raise AgentRunBusyError(f"Session '{session_id}' already has an active run.")
        async with lock:
            return self.session_manager.set_selected_model(session_id, selection)

    async def set_selected_skills(
        self,
        session_id: str,
        skill_ids: Sequence[str],
    ) -> dict[str, Any]:
        """Validate and persist selected skills only when no run is active."""
        if self.skills_manager:
            self.skills_manager.fingerprint(skill_ids)
        elif skill_ids:
            raise ValueError("Agent Skills are not available.")
        lock = self._session_locks.setdefault(session_id, asyncio.Lock())
        if lock.locked():
            raise AgentRunBusyError(f"Session '{session_id}' already has an active run.")
        async with lock:
            return self.session_manager.set_selected_skills(session_id, skill_ids)

    async def apply_settings_update(
        self,
        settings: FoundrySettings,
        persist: Callable[[], FoundrySettings],
    ) -> tuple[FoundrySettings, list[dict[str, Any]]]:
        """Persist settings and reconcile affected sessions under their run locks."""
        affected = [
            session_id
            for session_id in self.session_manager.sessions
            if not settings.selection_exists(self.session_manager.selected_model(session_id))
        ]
        locks = [self._session_locks.setdefault(session_id, asyncio.Lock()) for session_id in affected]
        if any(lock.locked() for lock in locks):
            raise AgentRunBusyError(
                "Foundry settings cannot remove a model used by an active run. "
                "Cancel or complete the run and save again."
            )
        acquired: list[asyncio.Lock] = []
        try:
            for lock in locks:
                await lock.acquire()
                acquired.append(lock)
            persisted = persist()
            updated = [
                self.session_manager.set_selected_model(
                    session_id,
                    persisted.default_selection,
                    touch=False,
                )
                for session_id in affected
            ]
            return persisted, updated
        finally:
            for lock in reversed(acquired):
                lock.release()

    async def shutdown(self) -> None:
        tasks = [task for task in self._active_tasks.values() if not task.done()]
        for run_request_id in list(self._pending_approvals):
            self._cancel_pending_approval(run_request_id)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _cancel_pending_approval(self, run_request_id: str) -> None:
        pending = self._pending_approvals.pop(run_request_id, None)
        if pending and not pending.future.done():
            pending.future.cancel()

    def _build_replay_messages_with_attachments(
        self,
        session_id: str,
        settings: ResolvedFoundrySettings,
        *,
        exclude_message_id: str | None = None,
    ) -> list[Message]:
        session = self.session_manager.get_session(session_id)
        if session is None:
            return []
        replay: list[Message] = []
        for item in self.session_manager.replay_message_records(session):
            if item.get("id") == exclude_message_id:
                continue
            contents: list[Content | str] = []
            records = item.get("attachments")
            if item["role"] == "user" and isinstance(records, list) and records:
                attachments = self.session_manager.load_attachments(session_id, records)
                contents.extend(build_attachment_contents(attachments, settings.api_type))
            contents.append(item["content"])
            replay.append(Message(role=item["role"], contents=contents))
        return replay

    @staticmethod
    def _approval_payload(request: Content) -> dict[str, Any]:
        call = request.function_call
        return {
            "id": request.id,
            "name": call.name if call else "",
            "arguments": AgentRuntime._safe_json(call.arguments if call else None),
            "serverLabel": (
                (call.additional_properties or {}).get("server_label") if call else None
            ),
        }

    @staticmethod
    def _safe_json(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return value

    @staticmethod
    def _safe_error_message(exc: Exception, api_key: str | None = None) -> str:
        message = str(exc)
        if api_key:
            message = message.replace(api_key, "[REDACTED]")
        message = re.sub(
            r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+",
            r"\1[REDACTED]",
            message,
        )
        message = re.sub(
            r"(?i)(api[-_ ]?key\s*[:=]\s*)[^\s,;]+",
            r"\1[REDACTED]",
            message,
        )
        return message[:1000]

    @staticmethod
    def _extract_forced_tool(message: str, selected_tool_ids: Sequence[str]) -> tuple[str | None, str]:
        if not message.startswith("#"):
            return None, message
        token, separator, remainder = message[1:].partition(" ")
        if token in selected_tool_ids:
            clean = remainder.lstrip() if separator else ""
            return token, clean or "Use the selected tool."
        return None, message


__all__ = [
    "AgentRunBusyError",
    "AgentRuntime",
    "AgentRuntimeError",
    "ApprovalDecision",
    "ApprovalNotFoundError",
    "RunResult",
    "RunReservation",
]
