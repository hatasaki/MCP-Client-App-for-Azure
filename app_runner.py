"""Standalone launcher that starts the FastAPI/Socket.IO backend and opens the UI in a built-in WebView.
"""
from __future__ import annotations

import argparse
import os
import threading
import sys
from pathlib import Path
import json
import tempfile
import traceback

import time

# Redirect stdout/stderr to a custom null device with write/flush methods
import io
class _Null(io.TextIOBase):
    """File-like sink that safely satisfies logging handlers expecting fileno()."""
    def __init__(self):
        # Open the platform null device once so fileno() returns a valid FD
        self._fd = os.open(os.devnull, os.O_WRONLY)

    def write(self, *a, **k):
        return 0

    def flush(self):
        pass

    def fileno(self):  # type: ignore[override]
        return self._fd

    def isatty(self):  # type: ignore[override]
        return False

# --------------------------------------------------
# Helper: load user-defined port from config file
# --------------------------------------------------
def _load_config_port() -> int | None:
    """Return port number from user config, if available and valid."""
    try:
        if CONFIG_PATH.is_file():
            with CONFIG_PATH.open(encoding="utf-8") as fp:
                data = json.load(fp)
            port = data.get("port")
            if isinstance(port, int) and 0 < port < 65536:
                return port
            # Allow string that looks like an int as well, e.g. env-based templating
            if isinstance(port, str) and port.isdigit():
                num = int(port)
                if 0 < num < 65536:
                    return num
    except Exception:  # Broad except: ignore malformed config
        pass
    return None

# --------------------------------------------------
# Configuration
# --------------------------------------------------

BACKEND_APP_IMPORT = "backend.main:app"  # ASGI application path
WINDOW_TITLE = "MCP Client for Microsoft Foundry"
LOADING_PAGE = Path(__file__).with_name("assets").joinpath("loading.html")
# Path to user configuration file (used by both port and logging helpers)
CONFIG_PATH = Path.home() / ".mcpclient" / "mcpclient.conf"

HOST = "127.0.0.1"
_DEFAULT_PORT = 3001
PORT: int = _load_config_port() or _DEFAULT_PORT
os.environ.setdefault("MCPCLIENT_CALLBACK_BASE_URL", f"http://{HOST}:{PORT}")

def _load_config_logfile() -> str | None:
    """Return logfile path from user config, if available and valid."""
    try:
        if CONFIG_PATH.is_file():
            with CONFIG_PATH.open(encoding="utf-8") as fp:
                data = json.load(fp)
            logfile = data.get("log_file")
            if isinstance(logfile, str) and logfile.strip():
                return logfile
    except Exception:  # Broad except: ignore malformed config
        pass
    return None


def run_backend() -> None:
    """Run the Uvicorn ASGI server (blocking)."""
    import uvicorn

    # "reload" is disabled for packaged executables
    uvicorn.run(BACKEND_APP_IMPORT, host=HOST, port=PORT, reload=False, log_level="info")


def wait_for_backend(window: object) -> None:
    """Poll the backend until it is reachable, then load it in the WebView."""
    import requests

    url = f"http://{HOST}:{PORT}"
    while True:
        try:
            requests.get(url, timeout=1)
            break
        except requests.RequestException:
            time.sleep(0.5)
    window.load_url(url)  # type: ignore[attr-defined]


def _setup_logging(log_path: str | None) -> None:
    """Redirect stdout/stderr to a file or suppress them when windowed."""
    if log_path:
        # Ensure destination directory exists
        os.makedirs(Path(log_path).expanduser().resolve().parent, exist_ok=True)
        fp = open(log_path, "a", encoding="utf-8", buffering=1)  # pylint: disable=consider-using-with
        sys.stdout = fp
        sys.stderr = fp
    else:
        # Windowed PyInstaller executables expose stdout/stderr as None.
        if sys.stdout is None or sys.stderr is None:
            sys.stdout = _Null()
            sys.stderr = _Null()


def main() -> None:
    parser = argparse.ArgumentParser(description="Launch MCP Client for Microsoft Foundry")
    default_log = _load_config_logfile()
    parser.add_argument(
        "--log",
        metavar="FILE",
        default=default_log,
        help="write stdout/stderr to file when windowed (overrides config)",
    )
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    args, _ = parser.parse_known_args()

    if args.smoke_test:
        # A windowed PyInstaller executable otherwise turns import failures
        # into a native modal that CI/terminal automation cannot read. Convert
        # every smoke failure into a traceback file and deterministic exit code.
        report_path = Path(
            os.environ.get("MCPCLIENT_SMOKE_REPORT")
            or (Path(tempfile.gettempdir()) / "mcpclient-smoke-report.txt")
        )
        try:
            from agent_framework_foundry import FoundryChatClient as _foundry_client  # noqa: F401
            from agent_framework_openai import (  # noqa: F401
                OpenAIChatClient as _responses_client,
                OpenAIChatCompletionClient as _chat_client,
            )
            from agent_framework_anthropic import AnthropicFoundryClient as _claude_client  # noqa: F401
            from backend.main import app as _backend_app  # noqa: F401
        except BaseException:
            report_path.write_text(traceback.format_exc(), encoding="utf-8")
            os._exit(1)
        report_path.write_text("Packaged import smoke test passed.\n", encoding="utf-8")
        os._exit(0)

    _setup_logging(args.log)
    import webview

    # Run backend in a daemon thread so the main thread can own the GUI
    threading.Thread(target=run_backend, daemon=True).start()

    # Show loading page while the backend starts
    try:
        loading_html = LOADING_PAGE.read_text(encoding="utf-8")
    except FileNotFoundError:
        loading_html = "<h1>MCP Client for Microsoft Foundry is starting…</h1>"

    window = webview.create_window(
        WINDOW_TITLE,
        html=loading_html,
        width=1200,
        height=800,
    )

    # wait_for_backend runs in a separate thread once the GUI loop starts
    webview.start(wait_for_backend, window)


if __name__ == "__main__":
    main()
