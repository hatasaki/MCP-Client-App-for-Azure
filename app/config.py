import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from app.foundry_config import (
    FoundrySettings,
    FoundrySettingsStore,
    FoundrySettingsWrite,
)

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
        settings: dict[str, Any] = {}
        if _USER_CONF_PATH.is_file():
            try:
                existing = json.loads(_USER_CONF_PATH.read_text(encoding="utf-8"))
                if isinstance(existing, dict):
                    settings.update(existing)
            except (OSError, json.JSONDecodeError):
                pass
        settings["data_dir"] = str(path)
        _USER_CONF_PATH.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
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
        dir_selected = _fd.askdirectory(
            title="Select data directory for MCP Client for Microsoft Foundry",
            initialdir=str(initial_dir or Path.home()),
        )
        root.destroy()
        if dir_selected:
            return Path(dir_selected)
    except Exception as exc:  # noqa: BLE001 broad but safe; GUI may fail in headless env
        print(f"[WARN] GUI directory selection failed: {exc}", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# Determine DATA_DIR using saved config or user prompt, then fallback
# ---------------------------------------------------------------------------

_env_data_dir = os.environ.get("MCPCLIENT_DATA_DIR")
_saved_dir = Path(_env_data_dir).expanduser().resolve() if _env_data_dir else _load_saved_data_dir()
if _saved_dir is None:
    _chosen_dir = None if os.environ.get("MCPCLIENT_HEADLESS") == "1" else _prompt_user_for_data_dir()
    if _chosen_dir is None:
        _chosen_dir = _USER_CONF_DIR / "data"
        _chosen_dir.mkdir(parents=True, exist_ok=True)
        print(f"[INFO] Using default data directory: {_chosen_dir}", file=sys.stderr)
    _saved_dir = _chosen_dir
    _save_data_dir(_saved_dir)

_saved_dir.mkdir(parents=True, exist_ok=True)

# Finalize DATA_DIR (no fallback)
DATA_DIR = _saved_dir

# ---------------------------------------------------------------------------
# Paths inside the data directory
# ---------------------------------------------------------------------------

AZURE_CONF_PATH = DATA_DIR / "AzureOpenAI.json"
FOUNDRY_CONF_PATH = DATA_DIR / "FoundrySettings.json"
MCP_CONF_PATH = DATA_DIR / "mcp.json"

foundry_settings_store = FoundrySettingsStore(FOUNDRY_CONF_PATH, AZURE_CONF_PATH)


def load_foundry_settings() -> FoundrySettings | None:
    """Load current Foundry settings, migrating legacy Azure settings once."""
    ensure_data_dir()
    return foundry_settings_store.load()


def save_foundry_settings(settings: FoundrySettings) -> None:
    """Persist validated Foundry settings atomically."""
    ensure_data_dir()
    foundry_settings_store.save(settings)


def update_foundry_settings(payload: FoundrySettingsWrite) -> FoundrySettings:
    """Resolve secret actions and atomically persist Foundry settings."""
    ensure_data_dir()
    return foundry_settings_store.update(payload)


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


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
    conf = {srv.get("name", f"server{idx}"): {k: v for k, v in srv.items() if k != "name"}
            for idx, srv in enumerate(servers)}
    encoded = json.dumps({"servers": conf}, indent=2, ensure_ascii=False)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{MCP_CONF_PATH.name}.",
        suffix=".tmp",
        dir=str(MCP_CONF_PATH.parent),
        text=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.chmod(temporary_name, 0o600)
        except OSError:
            pass
        os.replace(temporary_name, MCP_CONF_PATH)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
