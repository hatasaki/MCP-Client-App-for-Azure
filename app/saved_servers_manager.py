from typing import Any, Dict, List
from app.config import load_mcp_servers, save_mcp_servers

class SavedServersManager:
    def __init__(self):
        self.servers: List[Dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        """Load saved MCP servers from data/mcp.json."""
        self.servers = load_mcp_servers()

    def list(self) -> List[Dict[str, Any]]:
        # Always refresh from disk to reflect external updates
        self._load()
        return self.servers

    def _write(self):
        """Persist the current server list back to data/mcp.json."""
        save_mcp_servers(self.servers)

    def save_server(self, server: Dict[str, Any]):
        idx = next((i for i, s in enumerate(self.servers) if s.get("name") == server.get("name")), None)
        if idx is not None:
            self.servers[idx] = server
        else:
            self.servers.append(server)
        self._write()

    def delete_saved(self, name: str):
        # Refresh latest list from disk first
        self._load()
        self.servers = [s for s in self.servers if s.get("name") != name]
        self._write()
        return self.servers

    # backward-compat aliases
    def get_saved(self):
        return self.list()
    def save(self, server: Dict[str, Any]):
        self.save_server(server)
    def delete(self, name:str):
        return self.delete_saved(name)
