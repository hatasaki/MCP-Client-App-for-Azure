import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4
from datetime import datetime

from app.azure_openai_service import AzureOpenAIService
from app.mcp_manager import MCPManager
from app.config import DATA_DIR, ensure_data_dir

# Ensure the data directory exists before working with session files
ensure_data_dir()
SESSIONS_PATH = (DATA_DIR / "sessions")
SESSIONS_PATH.mkdir(parents=True, exist_ok=True)

class SessionManager:
    def __init__(self):
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self._load_sessions()

    def _load_sessions(self) -> None:
        for f in SESSIONS_PATH.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                self.sessions[data["id"]] = data
            except Exception:
                f.unlink()

    def list_sessions(self) -> List[Dict[str, Any]]:
        return list(self.sessions.values())

    # Unified creator used by backend for compatibility
    def create(self, sid: str | None = None):
        return self.create_session(sid)

    def create_session(self, sid: str | None = None) -> Dict[str, Any]:
        sid = sid or str(uuid4())
        now = datetime.utcnow().isoformat() + "Z"
        now_local = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        session = {"id": sid, "name": f"Chat {now_local}", "messages": [], "createdAt": now, "updatedAt": now, "autoApproveAll": False, "responseId": None}
        self.sessions[sid] = session
        self._save_session(session)
        return session

    def get_session(self, sid: str) -> Optional[Dict[str, Any]]:
        return self.sessions.get(sid)

    def update_session(self, sid: str, update: Dict[str, Any]) -> None:
        if sid not in self.sessions:
            return
        session=self.sessions[sid]
        session.update(update)
        # If the first message is from the user, use it as the session title
        if session['messages'] and session['name'].startswith('Chat'):
            first=session['messages'][0]
            if first['role']=='user':
                txt=first['content'][:30]
                session['name']=txt if txt else session['name']
        session['updatedAt']=datetime.utcnow().isoformat()+"Z"
        self._save_session(session)

    def delete_session(self, sid: str) -> None:
        if sid in self.sessions:
            del self.sessions[sid]
            f = SESSIONS_PATH / f"{sid}.json"
            if f.exists():
                f.unlink()

    def _save_session(self, session: Dict[str, Any]) -> None:
        f = SESSIONS_PATH / f"{session['id']}.json"
        f.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")

    def list(self):
        """Alias for TypeScript backend compatibility."""
        return self.list_sessions()

    def get(self, sid: str):
        """Alias for TypeScript backend compatibility."""
        return self.get_session(sid)

    def delete(self, sid: str):
        """Alias for older backend compatibility. Deletes a session by ID."""
        return self.delete_session(sid)
