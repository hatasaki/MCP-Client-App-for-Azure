"""Standalone launcher that starts the FastAPI/Socket.IO backend and opens the UI in a built-in WebView.
"""
from __future__ import annotations

import argparse
import os
import threading
import sys
from pathlib import Path

import time
import requests
import uvicorn
import webview

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
# Configuration
# --------------------------------------------------
HOST = "127.0.0.1"
PORT = 3001
BACKEND_APP_IMPORT = "backend.main:app"  # ASGI application path
WINDOW_TITLE = "MCP Client for Azure"
LOADING_PAGE = Path(__file__).with_name("assets").joinpath("loading.html")


def run_backend() -> None:
    """Run the Uvicorn ASGI server (blocking)."""
    # "reload" is disabled for packaged executables
    uvicorn.run(BACKEND_APP_IMPORT, host=HOST, port=PORT, reload=False, log_level="info")


def wait_for_backend(window: webview.Window) -> None:
    """Poll the backend until it is reachable, then load it in the WebView."""
    url = f"http://{HOST}:{PORT}"
    while True:
        try:
            requests.get(url, timeout=1)
            break
        except requests.RequestException:
            time.sleep(0.5)
    window.load_url(url)


def _setup_logging(log_path: str | None) -> None:
    """Redirect stdout/stderr to a file or suppress them when windowed."""
    if log_path:
        # Ensure destination directory exists
        os.makedirs(Path(log_path).expanduser().resolve().parent, exist_ok=True)
        fp = open(log_path, "a", encoding="utf-8", buffering=1)  # pylint: disable=consider-using-with
        sys.stdout = fp
        sys.stderr = fp
    else:
        # In windowed mode suppress console output unless the user provided --log
        if sys.stdout is not sys.__stdout__:
            sys.stdout = _Null()
            sys.stderr = _Null()


def main() -> None:
    parser = argparse.ArgumentParser(description="Launch MCP Client for Azure")
    parser.add_argument("--log", metavar="FILE", help="write stdout/stderr to file when windowed")
    args, _ = parser.parse_known_args()

    _setup_logging(args.log)

    # Run backend in a daemon thread so the main thread can own the GUI
    threading.Thread(target=run_backend, daemon=True).start()

    # Show loading page while the backend starts
    try:
        loading_html = LOADING_PAGE.read_text(encoding="utf-8")
    except FileNotFoundError:
        loading_html = "<h1>MCP Client for Azure is starting…</h1>"

    window = webview.create_window(
        WINDOW_TITLE,
        html=loading_html,
        width=1200,
        height=800,
    )

    # wait_for_backend runs in a separate thread once the GUI loop starts
    webview.start(wait_for_backend, window)


# Import ensures backend package is bundled by PyInstaller
try:
    import backend.main  # noqa: F401  # pylint: disable=unused-import
except ImportError:
    pass

if __name__ == "__main__":
    main()
