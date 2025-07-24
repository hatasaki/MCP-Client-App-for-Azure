import json
import sys
from pathlib import Path
from typing import Any, Dict, List

# NOTE: The actual DATA_DIR will be determined later after checking user configuration.

# ---------------------------------------------------------------------------
# User-level configuration for data directory
# ---------------------------------------------------------------------------

_USER_CONF_DIR = Path.home() / ".mcpclient"
_USER_CONF_PATH = _USER_CONF_DIR / "mcpclient.conf"


def _load_saved_data_dir() -> Path | None:  # Python 3.10 union syntax compatible (py311+ in project)
    """Load previously saved data directory from the user's config file.
    Returns the path if it exists and is a directory, otherwise *None*."""
    try:
        if _USER_CONF_PATH.is_file():
            content = json.loads(_USER_CONF_PATH.read_text(encoding="utf-8"))
            if isinstance(content, dict):
                raw = content.get("data_dir")
                if raw and isinstance(raw, str):
                    p = Path(raw).expanduser().resolve()
                    if p.is_dir():
                        return p
    except Exception:
        # Malformed file – ignore and fall through to prompt/fallback
        pass
    return None


def _save_data_dir(path: Path) -> None:
    """Persist selected *path* to the user config file."""
    try:
        _USER_CONF_DIR.mkdir(parents=True, exist_ok=True)
        _USER_CONF_PATH.write_text(json.dumps({"data_dir": str(path)}, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        # Non-fatal; continue without saving
        print("[WARN] Failed to write mcpclient.conf", file=sys.stderr)


# ---------------------------------------------------------------------------
# Prompt user for data directory (GUI)
# ---------------------------------------------------------------------------

def _prompt_user_for_data_dir(initial_dir: Path | None = None) -> Path | None:
    """Show a GUI directory-chooser dialog and return the selected path or *None* if cancelled."""
    try:
        import tkinter as _tk
        from tkinter import filedialog as _fd

        root = _tk.Tk()
        root.withdraw()  # Hide the main window
        # Windows: keep the dialog on top
        root.attributes("-topmost", True)
        dir_selected = _fd.askdirectory(title="Select data directory for MCP Client for Azure", initialdir=str(initial_dir or Path.home()))
        root.destroy()
        if dir_selected:
            return Path(dir_selected)
    except Exception as exc:  # noqa: BLE001 broad but safe; GUI may fail in headless env
        print(f"[WARN] GUI directory selection failed: {exc}", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# Determine DATA_DIR using saved config or user prompt, then fallback
# ---------------------------------------------------------------------------

_saved_dir = _load_saved_data_dir()
if _saved_dir is None:
    _chosen_dir = _prompt_user_for_data_dir()
    if _chosen_dir is None:
        print("[ERROR] Data directory was not selected. Exiting.", file=sys.stderr)
        sys.exit(1)
    _saved_dir = _chosen_dir
    _save_data_dir(_saved_dir)

# Finalize DATA_DIR (no fallback)
DATA_DIR = _saved_dir

# ---------------------------------------------------------------------------
# Paths inside the data directory
# ---------------------------------------------------------------------------

AZURE_CONF_PATH = DATA_DIR / "AzureOpenAI.json"
MCP_CONF_PATH = DATA_DIR / "mcp.json"

# Default system prompt
DEFAULT_SYSTEM_PROMPT = (
    "Based on the user's instructions, analyze the user's intent, define goals to achieve that intent, "
    "invoke and execute necessary tools until the goals are accomplished, and finally return the response to the user."
)


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_or_create_azure_conf() -> Dict[str, Any]:
    ensure_data_dir()
    if AZURE_CONF_PATH.exists():
        # ensure required keys exist
        data = json.loads(AZURE_CONF_PATH.read_text(encoding="utf-8"))
        changed = False
        # Ensure keys exist even if blank so client round-trips preserve empties
        for k in ("temperature", "top_p", "max_tokens"):
            if k not in data:
                data[k] = ""
                changed = True
        if changed:
            AZURE_CONF_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return data
    # Create default config
    cfg = {
        "endpoint": "",
        "api_key": "",
        "api_version": "",
        "deployment": "",
        # Prompt and generation parameters (added temperature, top_p, max_tokens)
        # Default generation parameters
        "system_prompt": DEFAULT_SYSTEM_PROMPT,
        "temperature": "",
        "top_p": "",
        "max_tokens": "",
    }
    AZURE_CONF_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    return cfg


def save_azure_conf(cfg: Dict[str, Any]) -> None:
    ensure_data_dir()
    AZURE_CONF_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def load_mcp_servers() -> List[Dict[str, Any]]:
    ensure_data_dir()
    if MCP_CONF_PATH.exists():
        data = json.loads(MCP_CONF_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "servers" in data:
            servers = data.get("servers", [])
            if isinstance(servers, dict):
                # convert dict to list
                return [{"name": name, **cfg} for name, cfg in servers.items()]
            elif isinstance(servers, list):
                return servers
        if isinstance(data, dict):
            return [{"name": name, **cfg} for name, cfg in data.items()]
        if isinstance(data, list):
            return data
    return []


def save_mcp_servers(servers: List[Dict[str, Any]]) -> None:
    ensure_data_dir()
    # Save as dict with names
    conf = {srv.get("name", f"server{idx}"): {k: v for k, v in srv.items() if k != "name"}
            for idx, srv in enumerate(servers)}
    MCP_CONF_PATH.write_text(json.dumps({"servers": conf}, indent=2, ensure_ascii=False), encoding="utf-8")
