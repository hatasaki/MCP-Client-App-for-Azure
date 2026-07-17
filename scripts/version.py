from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VERSION_FILE = ROOT / "version_info.txt"
_VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")

_PATTERNS = {
    "filevers": re.compile(r"(?m)^(\s*filevers=\()(\d+),\s*(\d+),\s*(\d+),\s*(\d+)(\),\s*)$"),
    "prodvers": re.compile(r"(?m)^(\s*prodvers=\()(\d+),\s*(\d+),\s*(\d+),\s*(\d+)(\),\s*)$"),
    "FileVersion": re.compile(r"(StringStruct\('FileVersion',\s*')([^']+)('\))"),
    "ProductVersion": re.compile(r"(StringStruct\('ProductVersion',\s*')([^']+)('\))"),
}


class VersionError(ValueError):
    """Raised when version_info.txt is invalid or inconsistent."""


def parse_version(value: str) -> tuple[int, int, int, int]:
    match = _VERSION_RE.fullmatch(value.strip())
    if not match:
        raise VersionError("Version must contain exactly four numeric components, for example 0.5.0.0.")
    parts = tuple(int(part) for part in match.groups())
    if any(part > 65535 for part in parts):
        raise VersionError("Each Windows version component must be between 0 and 65535.")
    return parts  # type: ignore[return-value]


def format_version(parts: Sequence[int]) -> str:
    return ".".join(str(part) for part in parts)


def read_versions(path: Path = DEFAULT_VERSION_FILE) -> dict[str, tuple[int, int, int, int]]:
    text = path.read_text(encoding="utf-8")
    values: dict[str, tuple[int, int, int, int]] = {}
    for field, pattern in _PATTERNS.items():
        matches = list(pattern.finditer(text))
        if len(matches) != 1:
            raise VersionError(f"Expected exactly one {field} entry in {path}; found {len(matches)}.")
        match = matches[0]
        if field in {"filevers", "prodvers"}:
            parts = tuple(int(match.group(index)) for index in range(2, 6))
            if any(part > 65535 for part in parts):
                raise VersionError(f"{field} contains a component greater than 65535.")
            values[field] = parts  # type: ignore[assignment]
        else:
            values[field] = parse_version(match.group(2))
    return values


def current_version(path: Path = DEFAULT_VERSION_FILE) -> tuple[int, int, int, int]:
    values = read_versions(path)
    unique = set(values.values())
    if len(unique) != 1:
        rendered = ", ".join(f"{field}={format_version(value)}" for field, value in values.items())
        raise VersionError(f"Version entries are inconsistent: {rendered}")
    return unique.pop()


def set_version(value: str, path: Path = DEFAULT_VERSION_FILE) -> tuple[int, int, int, int]:
    parts = parse_version(value)
    version = format_version(parts)
    text = path.read_text(encoding="utf-8")

    replacements = {
        "filevers": lambda match: f"{match.group(1)}{', '.join(str(part) for part in parts)}{match.group(6)}",
        "prodvers": lambda match: f"{match.group(1)}{', '.join(str(part) for part in parts)}{match.group(6)}",
        "FileVersion": lambda match: f"{match.group(1)}{version}{match.group(3)}",
        "ProductVersion": lambda match: f"{match.group(1)}{version}{match.group(3)}",
    }
    for field, pattern in _PATTERNS.items():
        text, count = pattern.subn(replacements[field], text)
        if count != 1:
            raise VersionError(f"Expected exactly one {field} entry in {path}; found {count}.")

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)

    verified = current_version(path)
    if verified != parts:
        raise VersionError("Version update did not verify successfully.")
    return verified


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read, validate, or update version_info.txt consistently.")
    parser.add_argument("--file", type=Path, default=DEFAULT_VERSION_FILE, help=argparse.SUPPRESS)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("get", help="Print the validated current version.")
    verify_parser = subparsers.add_parser("verify", help="Validate all version entries.")
    verify_parser.add_argument("--newer-than", help="Also require the current version to be greater than this version.")
    set_parser = subparsers.add_parser("set", help="Update all version entries atomically.")
    set_parser.add_argument("version", help="Four-component version, for example 0.5.0.0.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "set":
            version = set_version(args.version, args.file)
        else:
            version = current_version(args.file)
            if args.command == "verify" and args.newer_than:
                baseline = parse_version(args.newer_than)
                if version <= baseline:
                    raise VersionError(
                        f"Version {format_version(version)} must be greater than {format_version(baseline)}."
                    )
    except (OSError, VersionError) as exc:
        print(f"Version validation failed: {exc}", file=sys.stderr)
        return 1
    print(format_version(version))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
