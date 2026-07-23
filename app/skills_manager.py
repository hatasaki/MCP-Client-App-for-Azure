from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import unicodedata
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from agent_framework import FileSkillsSource, SkillsProvider, SkillsSourceContext

MAX_SKILL_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_SKILL_ARCHIVE_ENTRIES = 500
MAX_SKILL_ARCHIVE_FILE_BYTES = 5 * 1024 * 1024
MAX_SKILL_ARCHIVE_EXPANDED_BYTES = 25 * 1024 * 1024
MAX_SKILL_PATH_LENGTH = 240
MAX_SKILL_TEXT_BYTES = 1 * 1024 * 1024
MAX_SKILL_RESOURCE_COUNT = 200
MAX_SKILL_RESOURCE_BYTES = 2 * 1024 * 1024
MAX_SKILL_TOTAL_RESOURCE_BYTES = 10 * 1024 * 1024
MAX_SKILLS_PER_UPLOAD = 50
MAX_INSTALLED_SKILLS = 100
MAX_SELECTED_SKILLS = 20
SKILLS_SCHEMA_VERSION = 1

SKILL_FILENAME = "SKILL.md"
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
RESOURCE_EXTENSIONS = (".md", ".json", ".yaml", ".yml", ".csv", ".xml", ".txt")
_FORBIDDEN_MEMBER_TYPES = {stat.S_IFLNK, stat.S_IFCHR, stat.S_IFBLK, stat.S_IFIFO, stat.S_IFSOCK}
_WINDOWS_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:")
_WINDOWS_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}

READ_ONLY_SKILLS_INSTRUCTION_PROMPT = """You have access to read-only Agent Skills.
Each skill provides specialized instructions and optional text resources.

<available_skills>
{skills}
</available_skills>

When a task aligns with a skill's domain:
- Use `load_skill` to retrieve its instructions.
- Follow the provided guidance.
{resource_instructions}
Uploaded skill scripts are unavailable and cannot be executed.
Only load what is needed, when it is needed."""


class SkillLibraryError(ValueError):
    """Raised when a skill upload or library operation is invalid."""


class ReadOnlySkillsProvider(SkillsProvider):
    """MAF SkillsProvider variant that never exposes run_skill_script."""

    def _create_tools(self, skills):  # type: ignore[override]
        return [
            tool
            for tool in super()._create_tools(skills)
            if tool.name != self.RUN_SKILL_SCRIPT_TOOL_NAME
        ]


@dataclass(frozen=True, slots=True)
class SkillDefinition:
    id: str
    name: str
    description: str
    directory: Path
    content_hash: str
    resource_count: int
    resource_bytes: int
    scripts_ignored: bool
    source_filename: str

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "contentHash": self.content_hash,
            "resourceCount": self.resource_count,
            "resourceBytes": self.resource_bytes,
            "scriptsIgnored": self.scripts_ignored,
            "sourceFilename": self.source_filename,
        }


@dataclass(frozen=True, slots=True)
class _PreparedSkill:
    name: str
    description: str
    directory: Path
    content_hash: str
    resource_count: int
    resource_bytes: int
    scripts_ignored: bool
    source_filename: str


class SkillsManager:
    """Persistent, validated file-based MAF Agent Skills library."""

    def __init__(self, root: Path):
        self.root = root
        self.library_path = root / "library"
        self.manifest_path = root / "skills.json"
        self.root.mkdir(parents=True, exist_ok=True)
        self.library_path.mkdir(parents=True, exist_ok=True)
        for directory in (self.root, self.library_path):
            try:
                os.chmod(directory, 0o700)
            except OSError:
                pass
        self._lock = asyncio.Lock()
        self._skills: dict[str, SkillDefinition] = {}
        self._load_manifest()

    def list(self) -> list[dict[str, Any]]:
        return [skill.public_dict() for skill in sorted(self._skills.values(), key=lambda item: item.name)]

    def ids(self) -> set[str]:
        return set(self._skills)

    def fingerprint(self, skill_ids: Sequence[str]) -> str:
        selected = self._resolve(skill_ids)
        canonical = json.dumps(
            [(skill.id, skill.content_hash) for skill in selected],
            ensure_ascii=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def create_provider(self, skill_ids: Sequence[str]) -> SkillsProvider | None:
        selected = self._resolve(skill_ids)
        if not selected:
            return None
        # Uploaded skills are instruction/resource-only. Scripts are never
        # registered, and read-only MAF skill tools do not require approval.
        return ReadOnlySkillsProvider.from_paths(
            skill_paths=[skill.directory for skill in selected],
            resource_extensions=RESOURCE_EXTENSIONS,
            script_extensions=(".mcpclient-disabled",),
            search_depth=2,
            script_filter=lambda _skill_name, _path: False,
            resource_filter=self._resource_filter,
            instruction_template=READ_ONLY_SKILLS_INSTRUCTION_PROMPT,
            disable_load_skill_approval=True,
            disable_read_skill_resource_approval=True,
            source_id="uploaded_agent_skills",
        )

    async def upload(self, filename: str, data: bytes) -> list[dict[str, Any]]:
        async with self._lock:
            prepared_root = Path(tempfile.mkdtemp(prefix=".skills-upload-", dir=str(self.root)))
            try:
                prepared = await asyncio.to_thread(self._prepare_upload, filename, data, prepared_root)
                prospective = self.ids() | {item.name for item in prepared}
                if len(prospective) > MAX_INSTALLED_SKILLS:
                    raise SkillLibraryError("The library can contain at most 100 skills.")
                self._replace_skills(prepared)
                self._save_manifest()
                return [self._skills[item.name].public_dict() for item in prepared]
            finally:
                shutil.rmtree(prepared_root, ignore_errors=True)

    async def delete(self, skill_id: str) -> None:
        async with self._lock:
            skill = self._skills.get(skill_id)
            if skill is None:
                raise SkillLibraryError(f"Skill '{skill_id}' was not found.")
            shutil.rmtree(skill.directory, ignore_errors=True)
            self._skills.pop(skill_id, None)
            self._save_manifest()

    async def validate_library(self) -> list[dict[str, Any]]:
        """Validate that the persisted library remains discoverable by MAF."""
        async with self._lock:
            valid: list[SkillDefinition] = []
            for skill in self._skills.values():
                parsed = await self._discover_with_maf(skill.directory)
                if len(parsed) != 1 or parsed[0].frontmatter.name != skill.name:
                    raise SkillLibraryError(f"Stored skill '{skill.name}' is no longer valid.")
                valid.append(skill)
            return [skill.public_dict() for skill in sorted(valid, key=lambda item: item.name)]

    def _resolve(self, skill_ids: Sequence[str]) -> list[SkillDefinition]:
        if isinstance(skill_ids, (str, bytes)):
            raise SkillLibraryError("selectedSkillIds must be an array.")
        if len(skill_ids) > MAX_SELECTED_SKILLS:
            raise SkillLibraryError("A chat can enable at most 20 skills.")
        selected: list[SkillDefinition] = []
        seen: set[str] = set()
        for skill_id in skill_ids:
            if not isinstance(skill_id, str) or not skill_id:
                raise SkillLibraryError("Every selected skill id must be a non-empty string.")
            if skill_id in seen:
                raise SkillLibraryError(f"Duplicate selected skill id: '{skill_id}'.")
            seen.add(skill_id)
            skill = self._skills.get(skill_id)
            if skill is None:
                raise SkillLibraryError(f"Selected skill '{skill_id}' was not found.")
            selected.append(skill)
        return sorted(selected, key=lambda item: item.name)

    def _prepare_upload(self, filename: str, data: bytes, prepared_root: Path) -> list[_PreparedSkill]:
        safe_name = self._safe_upload_filename(filename)
        if not data:
            raise SkillLibraryError("The uploaded skill file is empty.")
        if len(data) > MAX_SKILL_UPLOAD_BYTES:
            raise SkillLibraryError("The uploaded skill file exceeds the 10 MB limit.")

        suffix = Path(safe_name).suffix.lower()
        extracted_root = prepared_root / "extracted"
        extracted_root.mkdir()
        if suffix == ".zip":
            self._extract_zip(data, extracted_root)
            self._normalize_root_skill(extracted_root)
        elif safe_name.lower() == SKILL_FILENAME.lower() or suffix == ".md":
            self._prepare_single_skill_md(data, extracted_root)
        else:
            raise SkillLibraryError("Upload a .zip archive or a SKILL.md file.")

        skill_directories = self._discover_skill_directories(extracted_root)
        if not skill_directories:
            raise SkillLibraryError("No valid SKILL.md was found in the upload.")
        if len(skill_directories) > MAX_SKILLS_PER_UPLOAD:
            raise SkillLibraryError("A single upload can contain at most 50 skills.")

        prepared: list[_PreparedSkill] = []
        names: set[str] = set()
        for directory in skill_directories:
            definition = self._prepare_skill(directory, safe_name)
            if definition.name in names:
                raise SkillLibraryError(f"The upload contains duplicate skill name '{definition.name}'.")
            names.add(definition.name)
            prepared.append(definition)
        return sorted(prepared, key=lambda item: item.name)

    def _normalize_root_skill(self, extracted_root: Path) -> None:
        root_skill = extracted_root / SKILL_FILENAME
        if not root_skill.is_file():
            return
        try:
            content = root_skill.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as exc:
            raise SkillLibraryError("The root SKILL.md must be readable UTF-8 text.") from exc
        name = self._parse_frontmatter(content)["name"]
        destination = extracted_root / name
        if destination.exists():
            raise SkillLibraryError(f"The root skill conflicts with existing directory '{name}'.")
        destination.mkdir()
        for entry in list(extracted_root.iterdir()):
            if entry != destination:
                shutil.move(str(entry), destination / entry.name)

    def _prepare_single_skill_md(self, data: bytes, extracted_root: Path) -> None:
        try:
            content = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise SkillLibraryError("SKILL.md must be UTF-8 encoded.") from exc
        frontmatter = self._parse_frontmatter(content)
        directory = extracted_root / frontmatter["name"]
        directory.mkdir()
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass
        target = directory / SKILL_FILENAME
        target.write_text(content, encoding="utf-8", newline="\n")
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass

    def _prepare_skill(self, source_directory: Path, source_filename: str) -> _PreparedSkill:
        skill_file = source_directory / SKILL_FILENAME
        try:
            content = skill_file.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as exc:
            raise SkillLibraryError(f"{skill_file} must be a readable UTF-8 file.") from exc
        frontmatter = self._parse_frontmatter(content)
        name = frontmatter["name"]
        if source_directory.name != name:
            raise SkillLibraryError(
                f"Skill directory '{source_directory.name}' must match frontmatter name '{name}'."
            )

        scripts_ignored = False
        for entry in list(source_directory.iterdir()):
            if entry.name.lower() == "scripts":
                scripts_ignored = True
                if entry.is_dir():
                    shutil.rmtree(entry)
                else:
                    entry.unlink()
        resource_count = 0
        resource_bytes = 0
        for path in source_directory.rglob("*"):
            if path.is_symlink():
                raise SkillLibraryError(f"Symlinks are not allowed in skills: {path.name}")
            if not path.is_file() or path == skill_file:
                continue
            relative = path.relative_to(source_directory)
            if len(relative.parts) > 2:
                raise SkillLibraryError(f"Skill path is deeper than supported: {relative.as_posix()}")
            if relative.parts[0].lower() not in {"references", "assets"}:
                if len(relative.parts) == 1 and relative.name.lower() in {
                    "license", "license.md", "license.txt"
                }:
                    if path.stat().st_size > MAX_SKILL_RESOURCE_BYTES:
                        raise SkillLibraryError("The bundled license file exceeds the 2 MB limit.")
                    self._validate_resource_text(path)
                    continue
                raise SkillLibraryError(
                    f"Only references/ and assets/ resources are supported: {relative.as_posix()}"
                )
            if path.suffix.lower() not in RESOURCE_EXTENSIONS:
                raise SkillLibraryError(f"Unsupported skill resource type: {relative.as_posix()}")
            size = path.stat().st_size
            if size > MAX_SKILL_RESOURCE_BYTES:
                raise SkillLibraryError(f"Skill resource exceeds the 2 MB limit: {relative.as_posix()}")
            self._validate_resource_text(path)
            resource_count += 1
            resource_bytes += size
            if resource_count > MAX_SKILL_RESOURCE_COUNT:
                raise SkillLibraryError("A skill can contain at most 200 resources.")
            if resource_bytes > MAX_SKILL_TOTAL_RESOURCE_BYTES:
                raise SkillLibraryError("A skill's resources exceed the 10 MB combined limit.")

        parsed = asyncio.run(self._discover_with_maf(source_directory))
        if len(parsed) != 1:
            raise SkillLibraryError(f"Skill '{name}' could not be loaded by Microsoft Agent Framework.")
        maf_frontmatter = parsed[0].frontmatter
        if maf_frontmatter.name != name:
            raise SkillLibraryError(f"Skill '{name}' failed Microsoft Agent Framework validation.")
        canonical = self._hash_directory(source_directory)
        return _PreparedSkill(
            name=name,
            description=maf_frontmatter.description,
            directory=source_directory,
            content_hash=canonical,
            resource_count=resource_count,
            resource_bytes=resource_bytes,
            scripts_ignored=scripts_ignored,
            source_filename=source_filename,
        )

    def _replace_skills(self, prepared: Sequence[_PreparedSkill]) -> None:
        for item in prepared:
            destination = self.library_path / item.name
            existing = self._skills.get(item.name)
            temporary_destination = self.library_path / f".{item.name}.{uuid4().hex}.tmp"
            shutil.copytree(item.directory, temporary_destination)
            backup: Path | None = None
            try:
                if destination.exists():
                    backup = self.library_path / f".{item.name}.{uuid4().hex}.bak"
                    os.replace(destination, backup)
                os.replace(temporary_destination, destination)
                if backup:
                    shutil.rmtree(backup, ignore_errors=True)
            except BaseException:
                shutil.rmtree(temporary_destination, ignore_errors=True)
                if backup and backup.exists() and not destination.exists():
                    os.replace(backup, destination)
                raise
            self._skills[item.name] = SkillDefinition(
                id=item.name,
                name=item.name,
                description=item.description,
                directory=destination,
                content_hash=item.content_hash,
                resource_count=item.resource_count,
                resource_bytes=item.resource_bytes,
                scripts_ignored=item.scripts_ignored,
                source_filename=item.source_filename,
            )
            if existing and existing.directory != destination:
                shutil.rmtree(existing.directory, ignore_errors=True)

    def _load_manifest(self) -> None:
        if not self.manifest_path.exists():
            return
        try:
            payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            if payload.get("schemaVersion") != SKILLS_SCHEMA_VERSION:
                raise SkillLibraryError("Unsupported skills manifest schema version.")
            records = payload.get("skills")
            if not isinstance(records, list):
                raise SkillLibraryError("The skills manifest is invalid.")
            for record in records:
                if not isinstance(record, Mapping):
                    raise SkillLibraryError("The skills manifest contains an invalid record.")
                name = record.get("name")
                if not isinstance(name, str) or not SKILL_NAME_PATTERN.fullmatch(name):
                    raise SkillLibraryError("The skills manifest contains an invalid skill name.")
                directory = self.library_path / name
                if not directory.is_dir():
                    continue
                skill = SkillDefinition(
                    id=name,
                    name=name,
                    description=str(record.get("description") or ""),
                    directory=directory,
                    content_hash=str(record.get("contentHash") or ""),
                    resource_count=int(record.get("resourceCount") or 0),
                    resource_bytes=int(record.get("resourceBytes") or 0),
                    scripts_ignored=bool(record.get("scriptsIgnored", False)),
                    source_filename=str(record.get("sourceFilename") or ""),
                )
                if self._hash_directory(directory) != skill.content_hash:
                    continue
                self._skills[name] = skill
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise SkillLibraryError("The skills library manifest is invalid or corrupted.") from exc

    def _save_manifest(self) -> None:
        payload = {
            "schemaVersion": SKILLS_SCHEMA_VERSION,
            "skills": [skill.public_dict() for skill in sorted(self._skills.values(), key=lambda item: item.name)],
        }
        encoded = json.dumps(payload, ensure_ascii=False, indent=2)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".skills.", suffix=".tmp", dir=str(self.root), text=True
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
            os.replace(temporary_name, self.manifest_path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

    @staticmethod
    def _safe_upload_filename(value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise SkillLibraryError("The upload requires a filename.")
        name = re.split(r"[\\/]", value.strip())[-1]
        if not name or len(name) > 255 or any(ord(char) < 32 for char in name):
            raise SkillLibraryError("The uploaded filename is invalid.")
        return name

    @staticmethod
    def _extract_zip(data: bytes, destination: Path) -> None:
        archive_stream = tempfile.SpooledTemporaryFile(max_size=MAX_SKILL_UPLOAD_BYTES)
        try:
            archive_stream.write(data)
            archive_stream.seek(0)
            with zipfile.ZipFile(archive_stream) as archive:
                members = archive.infolist()
                if len(members) > MAX_SKILL_ARCHIVE_ENTRIES:
                    raise SkillLibraryError("The ZIP contains more than 500 entries.")
                total = 0
                actual_total = 0
                seen: set[str] = set()
                for member in members:
                    relative = SkillsManager._safe_archive_path(member.filename)
                    normalized = unicodedata.normalize("NFC", relative.as_posix()).casefold()
                    if normalized in seen:
                        raise SkillLibraryError(f"The ZIP contains duplicate path '{relative.as_posix()}'.")
                    seen.add(normalized)
                    mode = member.external_attr >> 16
                    if mode and stat.S_IFMT(mode) in _FORBIDDEN_MEMBER_TYPES:
                        raise SkillLibraryError(f"Special files are not allowed in ZIPs: {relative.as_posix()}")
                    if member.flag_bits & 0x1:
                        raise SkillLibraryError("Encrypted ZIP entries are not supported.")
                    if member.is_dir():
                        continue
                    if member.file_size > MAX_SKILL_ARCHIVE_FILE_BYTES:
                        raise SkillLibraryError(f"ZIP entry exceeds the 5 MB limit: {relative.as_posix()}")
                    total += member.file_size
                    if total > MAX_SKILL_ARCHIVE_EXPANDED_BYTES:
                        raise SkillLibraryError("Expanded ZIP content exceeds the 25 MB limit.")
                    target = destination.joinpath(*relative.parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        os.chmod(target.parent, 0o700)
                    except OSError:
                        pass
                    with archive.open(member) as source, target.open("wb") as output:
                        actual_file_size = 0
                        while chunk := source.read(1024 * 1024):
                            actual_file_size += len(chunk)
                            actual_total += len(chunk)
                            if actual_file_size > MAX_SKILL_ARCHIVE_FILE_BYTES:
                                raise SkillLibraryError(
                                    f"Expanded ZIP entry exceeds the 5 MB limit: {relative.as_posix()}"
                                )
                            if actual_total > MAX_SKILL_ARCHIVE_EXPANDED_BYTES:
                                raise SkillLibraryError("Expanded ZIP content exceeds the 25 MB limit.")
                            output.write(chunk)
                    try:
                        os.chmod(target, 0o600)
                    except OSError:
                        pass
        except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, OSError) as exc:
            if isinstance(exc, SkillLibraryError):
                raise
            raise SkillLibraryError("The uploaded ZIP is invalid or unreadable.") from exc
        finally:
            archive_stream.close()

    @staticmethod
    def _safe_archive_path(value: str) -> PurePosixPath:
        normalized = value.replace("\\", "/")
        path = PurePosixPath(normalized)
        if (
            not normalized
            or normalized.startswith("/")
            or _WINDOWS_DRIVE_PATTERN.match(normalized)
            or any(part in {"", ".", ".."} for part in path.parts)
            or any(
                ":" in part
                or part.endswith((".", " "))
                or any(ord(char) < 32 for char in part)
                or part.split(".", 1)[0].lower() in _WINDOWS_RESERVED_NAMES
                for part in path.parts
            )
            or len(normalized) > MAX_SKILL_PATH_LENGTH
        ):
            raise SkillLibraryError(f"Unsafe ZIP path: '{value}'.")
        return path

    @staticmethod
    def _discover_skill_directories(root: Path) -> list[Path]:
        directories: list[Path] = []
        for skill_file in root.rglob(SKILL_FILENAME):
            relative = skill_file.relative_to(root)
            if len(relative.parts) > 3:
                raise SkillLibraryError(f"SKILL.md is nested too deeply: {relative.as_posix()}")
            directories.append(skill_file.parent)
        roots = set(directories)
        for directory in directories:
            if any(parent in roots for parent in directory.parents):
                raise SkillLibraryError("Nested skills are not supported inside another skill directory.")
        return sorted(directories)

    @staticmethod
    def _parse_frontmatter(content: str) -> dict[str, str]:
        if len(content.encode("utf-8")) > MAX_SKILL_TEXT_BYTES:
            raise SkillLibraryError("SKILL.md exceeds the 1 MB limit.")
        # Use the MAF parser through FileSkillsSource after staging. This small
        # parser obtains the directory name needed for staging a standalone file.
        match = re.match(r"^---\s*\r?\n(.*?)\r?\n---(?:\s*\r?\n|$)", content, re.DOTALL)
        if not match:
            raise SkillLibraryError("SKILL.md requires YAML frontmatter delimited by ---.")
        fields: dict[str, str] = {}
        for line in match.group(1).splitlines():
            if not line or line[0].isspace() or ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip().lower()
            value = value.strip().strip("\"'")
            if key == "name":
                fields[key] = value
        name = fields.get("name", "")
        if not SKILL_NAME_PATTERN.fullmatch(name) or len(name) > 64:
            raise SkillLibraryError("Skill name must follow the Agent Skills lowercase hyphenated naming rules.")
        if name in _WINDOWS_RESERVED_NAMES:
            raise SkillLibraryError("Skill name is reserved by Windows and is not portable.")
        return fields

    @staticmethod
    def _validate_resource_text(path: Path) -> None:
        try:
            path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            raise SkillLibraryError(f"Skill resource must be UTF-8 text: {path.name}") from exc

    @staticmethod
    def _hash_directory(directory: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(item for item in directory.rglob("*") if item.is_file()):
            relative = path.relative_to(directory).as_posix()
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        return digest.hexdigest()

    @staticmethod
    def _resource_filter(_skill_name: str, relative_path: str) -> bool:
        parts = PurePosixPath(relative_path).parts
        return bool(parts and parts[0].lower() in {"references", "assets"})

    @staticmethod
    async def _discover_with_maf(directory: Path):
        source = FileSkillsSource(
            directory,
            resource_extensions=RESOURCE_EXTENSIONS,
            script_extensions=(".mcpclient-disabled",),
            script_filter=lambda _skill_name, _path: False,
            resource_filter=SkillsManager._resource_filter,
        )

        class _Agent:
            name = "SkillValidationAgent"

        return await source.get_skills(SkillsSourceContext(agent=_Agent()))  # type: ignore[arg-type]


__all__ = [
    "MAX_SKILL_UPLOAD_BYTES",
    "MAX_SELECTED_SKILLS",
    "SkillDefinition",
    "SkillLibraryError",
    "SkillsManager",
]
