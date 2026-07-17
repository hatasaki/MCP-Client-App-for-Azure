from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from agent_framework import AgentSession, Message

from app.config import DATA_DIR, ensure_data_dir

SESSION_SCHEMA_VERSION = 2
COMPLETED_STATUS = "completed"
TRANSIENT_STATUSES = {"streaming", "running", "awaiting_approval"}
NON_REPLAYABLE_STATUSES = {"cancelled", "interrupted", "error", *TRANSIENT_STATUSES}

ensure_data_dir()
SESSIONS_PATH = DATA_DIR / "sessions"
SESSIONS_PATH.mkdir(parents=True, exist_ok=True)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class SessionManager:
    def __init__(self, sessions_path: Path | None = None, max_replay_characters: int = 200_000):
        self.sessions_path = sessions_path or SESSIONS_PATH
        self.sessions_path.mkdir(parents=True, exist_ok=True)
        self.max_replay_characters = max_replay_characters
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self._load_sessions()

    def _load_sessions(self) -> None:
        for file in self.sessions_path.glob("*.json"):
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
                session, changed = self._migrate_session(data)
                self.sessions[session["id"]] = session
                if changed:
                    self._save_session(session)
            except Exception:
                # Preserve unreadable files for recovery instead of deleting user data.
                corrupt = file.with_suffix(file.suffix + ".corrupt")
                if not corrupt.exists():
                    try:
                        file.replace(corrupt)
                    except OSError:
                        pass

    def _migrate_session(self, data: Dict[str, Any]) -> tuple[Dict[str, Any], bool]:
        changed = data.get("schemaVersion") != SESSION_SCHEMA_VERSION
        session = dict(data)
        session["schemaVersion"] = SESSION_SCHEMA_VERSION
        session.setdefault("messages", [])
        session.setdefault("autoApproveAll", False)
        session.setdefault("mafState", None)
        session.setdefault("preRunMafState", None)
        session.setdefault("configFingerprint", None)
        session.setdefault("stateEpoch", 0)
        session.pop("responseId", None)
        for raw in session["messages"]:
            if "id" not in raw:
                raw["id"] = str(uuid4())
                changed = True
            if "timestamp" not in raw:
                raw["timestamp"] = session.get("updatedAt") or _utc_now()
                changed = True
            status = raw.get("status")
            if status in TRANSIENT_STATUSES:
                raw["status"] = "interrupted"
                changed = True
            elif status is None:
                raw["status"] = COMPLETED_STATUS
                changed = True
            raw.setdefault("toolCalls", [])
        session.setdefault("createdAt", _utc_now())
        session.setdefault("updatedAt", session["createdAt"])
        return session, changed

    def list_sessions(self) -> List[Dict[str, Any]]:
        return [self.public_session(session) for session in self.sessions.values()]

    def create(self, sid: str | None = None) -> Dict[str, Any]:
        return self.create_session(sid)

    def create_session(self, sid: str | None = None) -> Dict[str, Any]:
        session_id = sid or str(uuid4())
        now = _utc_now()
        now_local = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        session: Dict[str, Any] = {
            "schemaVersion": SESSION_SCHEMA_VERSION,
            "id": session_id,
            "name": f"Chat {now_local}",
            "messages": [],
            "createdAt": now,
            "updatedAt": now,
            "autoApproveAll": False,
            "mafState": None,
            "preRunMafState": None,
            "configFingerprint": None,
            "stateEpoch": 0,
        }
        self.sessions[session_id] = session
        self._save_session(session)
        return self.public_session(session)

    def get_session(self, sid: str) -> Optional[Dict[str, Any]]:
        return self.sessions.get(sid)

    def get_public_session(self, sid: str) -> Optional[Dict[str, Any]]:
        session = self.get_session(sid)
        return self.public_session(session) if session else None

    def public_session(self, session: Dict[str, Any]) -> Dict[str, Any]:
        return {
            key: deepcopy(value)
            for key, value in session.items()
            if key not in {"mafState", "preRunMafState", "configFingerprint"}
        }

    def append_message(
        self,
        sid: str,
        *,
        role: str,
        content: str,
        status: str = COMPLETED_STATUS,
        message_id: str | None = None,
        tool_calls: list[str] | None = None,
    ) -> Dict[str, Any]:
        session = self._require(sid)
        message = {
            "id": message_id or str(uuid4()),
            "role": role,
            "content": content,
            "timestamp": _utc_now(),
            "status": status,
            "toolCalls": list(tool_calls or []),
        }
        session["messages"].append(message)
        self._touch_and_save(session)
        return deepcopy(message)

    def update_message(self, sid: str, message_id: str, **updates: Any) -> Dict[str, Any]:
        session = self._require(sid)
        message = next((item for item in session["messages"] if item.get("id") == message_id), None)
        if message is None:
            raise KeyError(f"Message '{message_id}' not found in session '{sid}'.")
        message.update(updates)
        self._touch_and_save(session)
        return deepcopy(message)

    def update_session(self, sid: str, update: Dict[str, Any]) -> None:
        session = self.sessions.get(sid)
        if session is None:
            return
        safe_update = dict(update)
        safe_update.pop("responseId", None)
        if "messages" in safe_update:
            migrated_messages = []
            for raw in safe_update["messages"]:
                item = dict(raw)
                item.setdefault("id", str(uuid4()))
                item.setdefault("timestamp", _utc_now())
                item.setdefault("status", COMPLETED_STATUS)
                item.setdefault("toolCalls", [])
                migrated_messages.append(item)
            safe_update["messages"] = migrated_messages
        session.update(safe_update)
        self._touch_and_save(session)

    def prepare_agent_session(self, sid: str, fingerprint: str) -> tuple[AgentSession, list[Message], bool]:
        """Restore matching MAF state or return normalized text history for replay."""
        session = self._require(sid)
        previous_fingerprint = session.get("configFingerprint")
        state_matches = previous_fingerprint == fingerprint
        maf_state = session.get("mafState") if state_matches else None
        replay: list[Message] = []
        state_reset = False
        if isinstance(maf_state, dict):
            try:
                agent_session = AgentSession.from_dict(deepcopy(maf_state))
            except Exception:
                agent_session = AgentSession(session_id=sid)
                replay = self.build_replay_messages(session)
                state_matches = False
                state_reset = True
        else:
            agent_session = AgentSession(session_id=sid)
            replay = self.build_replay_messages(session)
            state_matches = False
            # A brand-new empty session does not represent a reset. Existing
            # history or any previous fingerprint does.
            state_reset = bool(replay) or previous_fingerprint is not None
        if state_reset and previous_fingerprint is not None:
            session["stateEpoch"] = int(session.get("stateEpoch", 0)) + 1
        session["preRunMafState"] = deepcopy(session.get("mafState")) if state_matches else None
        if not state_matches:
            # Never persist old provider/session state under a new fingerprint.
            # This also makes an interrupted process restart safe.
            session["mafState"] = None
        session["configFingerprint"] = fingerprint
        self._touch_and_save(session)
        return agent_session, replay, state_reset

    def save_agent_session(self, sid: str, agent_session: AgentSession, fingerprint: str) -> None:
        session = self._require(sid)
        session["mafState"] = agent_session.to_dict()
        session["preRunMafState"] = None
        session["configFingerprint"] = fingerprint
        self._touch_and_save(session)

    def rollback_agent_session(self, sid: str) -> None:
        session = self._require(sid)
        session["mafState"] = deepcopy(session.get("preRunMafState"))
        session["preRunMafState"] = None
        self._touch_and_save(session)

    def build_replay_messages(self, session: Dict[str, Any]) -> list[Message]:
        candidates: list[dict[str, Any]] = []
        for item in session.get("messages", []):
            if item.get("role") not in {"user", "assistant"}:
                continue
            if item.get("status", COMPLETED_STATUS) in NON_REPLAYABLE_STATUSES:
                continue
            content = item.get("content")
            if not isinstance(content, str) or not content:
                continue
            candidates.append(item)

        selected: list[dict[str, Any]] = []
        used = 0
        for item in reversed(candidates):
            length = len(item["content"])
            if selected and used + length > self.max_replay_characters:
                break
            selected.append(item)
            used += length
        selected.reverse()
        return [Message(role=item["role"], contents=[item["content"]]) for item in selected]

    def delete_session(self, sid: str) -> None:
        if sid in self.sessions:
            del self.sessions[sid]
            file = self.sessions_path / f"{sid}.json"
            if file.exists():
                file.unlink()

    def _touch_and_save(self, session: Dict[str, Any]) -> None:
        if session["messages"] and session["name"].startswith("Chat"):
            first = next((message for message in session["messages"] if message.get("role") == "user"), None)
            if first and first.get("content"):
                session["name"] = first["content"][:30]
        session["updatedAt"] = _utc_now()
        self._save_session(session)

    def _save_session(self, session: Dict[str, Any]) -> None:
        target = self.sessions_path / f"{session['id']}.json"
        encoded = json.dumps(session, ensure_ascii=False, indent=2)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{session['id']}.", suffix=".tmp", dir=str(self.sessions_path), text=True
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, target)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

    def _require(self, sid: str) -> Dict[str, Any]:
        session = self.sessions.get(sid)
        if session is None:
            raise KeyError(f"Session '{sid}' not found.")
        return session

    def list(self) -> List[Dict[str, Any]]:
        return self.list_sessions()

    def get(self, sid: str) -> Optional[Dict[str, Any]]:
        return self.get_public_session(sid)

    def delete(self, sid: str) -> None:
        self.delete_session(sid)
