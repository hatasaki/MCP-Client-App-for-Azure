from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a packaged desktop import smoke test without GUI dialogs.")
    parser.add_argument("executable", type=Path)
    parser.add_argument("--timeout", type=int, default=120, help="Timeout in seconds (default: 120).")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    executable = args.executable.resolve()
    if not executable.is_file():
        print(f"Packaged executable does not exist: {executable}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="mcpclient-package-smoke-") as temporary_directory:
        temporary_path = Path(temporary_directory)
        report_path = temporary_path / "report.txt"
        data_path = temporary_path / "data"
        data_path.mkdir()
        environment = os.environ.copy()
        environment.update({
            "MCPCLIENT_HEADLESS": "1",
            "MCPCLIENT_DATA_DIR": str(data_path),
            "MCPCLIENT_SMOKE_REPORT": str(report_path),
        })
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        process = subprocess.Popen(
            [str(executable), "--smoke-test"],
            env=environment,
            creationflags=creation_flags,
            start_new_session=os.name != "nt",
        )
        try:
            return_code = process.wait(timeout=args.timeout)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                # A PyInstaller one-file launcher creates a child process. Kill
                # the whole tree so a failed smoke cannot lock dist/*.exe.
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait()
            print(f"Packaged smoke test timed out after {args.timeout}s: {executable}", file=sys.stderr)
            return 1

        report = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
        if report:
            print(report, end="" if report.endswith("\n") else "\n")
        if return_code != 0:
            if not report:
                print(
                    "The packaged process failed before writing its report. Check platform code-integrity/signing logs.",
                    file=sys.stderr,
                )
            print(f"Packaged smoke test failed with exit code {return_code}: {executable}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
