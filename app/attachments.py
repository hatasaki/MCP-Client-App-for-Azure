from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent_framework import Content
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.foundry_config import ApiType

MAX_ATTACHMENT_COUNT = 10
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_TOTAL_ATTACHMENT_BYTES = 25 * 1024 * 1024
MAX_EXTRACTED_TEXT_CHARACTERS = 200_000
MAX_PDF_PAGES_TO_EXTRACT = 200
SOCKET_MAX_HTTP_BUFFER_BYTES = 40 * 1024 * 1024
DEFAULT_ATTACHMENT_PROMPT = "Please analyze the attached file(s)."

SUPPORTED_ATTACHMENT_MEDIA_TYPES = frozenset({
    "application/pdf",
    "text/plain",
    "image/jpeg",
    "image/png",
})

_EXTENSION_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}
_MEDIA_TYPE_ALIASES = {
    "application/octet-stream": None,
    "image/jpg": "image/jpeg",
}
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


class AttachmentError(ValueError):
    """Raised when an attachment is invalid or cannot be prepared safely."""


@dataclass(frozen=True, slots=True)
class AttachmentData:
    name: str
    media_type: str
    data: bytes
    sha256: str
    attachment_id: str | None = None

    @property
    def size(self) -> int:
        return len(self.data)

    def public_record(self, attachment_id: str | None = None) -> dict[str, Any]:
        resolved_id = attachment_id or self.attachment_id
        if not resolved_id:
            raise AttachmentError("Stored attachments require an id.")
        return {
            "id": resolved_id,
            "filename": self.name,
            "mediaType": self.media_type,
            "sizeBytes": self.size,
            "contentHash": self.sha256,
        }


def parse_incoming_attachments(value: Any) -> list[AttachmentData]:
    """Validate the untrusted Socket.IO attachment payload and return canonical data."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise AttachmentError("attachments must be an array.")
    if len(value) > MAX_ATTACHMENT_COUNT:
        raise AttachmentError(f"A message can contain at most {MAX_ATTACHMENT_COUNT} attachments.")

    parsed: list[AttachmentData] = []
    total_size = 0
    for index, raw in enumerate(value, start=1):
        if not isinstance(raw, Mapping):
            raise AttachmentError(f"Attachment {index} must be an object.")
        name = _safe_filename(raw.get("name"), index)
        data = _attachment_bytes(raw.get("data"), index)
        if not data:
            raise AttachmentError(f"Attachment '{name}' is empty.")
        if len(data) > MAX_ATTACHMENT_BYTES:
            raise AttachmentError(f"Attachment '{name}' exceeds the 10 MB per-file limit.")

        declared_size = raw.get("size")
        if declared_size is not None and (
            isinstance(declared_size, bool)
            or not isinstance(declared_size, int)
            or declared_size != len(data)
        ):
            raise AttachmentError(f"Attachment '{name}' has inconsistent size metadata.")

        media_type = _canonical_media_type(name, raw.get("mediaType"))
        _validate_signature(name, media_type, data)
        digest = hashlib.sha256(data).hexdigest()
        parsed.append(AttachmentData(name, media_type, data, digest))
        total_size += len(data)
        if total_size > MAX_TOTAL_ATTACHMENT_BYTES:
            raise AttachmentError("Attachments exceed the 25 MB combined request limit.")
    return parsed


class AttachmentStore:
    """Content-addressed per-session attachment storage with no binary data in JSON."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def store(self, session_id: str, attachments: Sequence[AttachmentData]) -> list[dict[str, Any]]:
        if not attachments:
            return []
        directory = self.session_directory(session_id)
        directory.mkdir(parents=True, exist_ok=True)
        records: list[dict[str, Any]] = []
        for attachment in attachments:
            path = directory / attachment.sha256
            if not path.exists():
                self._atomic_write(path, attachment.data)
            records.append(attachment.public_record(str(uuid4())))
        return records

    def load(self, session_id: str, records: Sequence[Mapping[str, Any]]) -> list[AttachmentData]:
        loaded: list[AttachmentData] = []
        directory = self.session_directory(session_id)
        for index, record in enumerate(records, start=1):
            attachment_id = record.get("id")
            digest = record.get("contentHash")
            size = record.get("sizeBytes")
            if not isinstance(attachment_id, str) or not attachment_id:
                raise AttachmentError(f"Stored attachment {index} has no valid id.")
            if not isinstance(digest, str) or not _SHA256_PATTERN.fullmatch(digest):
                raise AttachmentError(f"Stored attachment '{attachment_id}' has an invalid digest.")
            if isinstance(size, bool) or not isinstance(size, int) or size < 1:
                raise AttachmentError(f"Stored attachment '{attachment_id}' has an invalid size.")
            path = directory / digest
            try:
                data = path.read_bytes()
            except OSError as exc:
                raise AttachmentError(f"Stored attachment '{attachment_id}' is unavailable.") from exc
            if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
                raise AttachmentError(f"Stored attachment '{attachment_id}' failed its integrity check.")
            name = _safe_filename(record.get("filename"), index)
            media_type = _canonical_media_type(name, record.get("mediaType"))
            _validate_signature(name, media_type, data)
            loaded.append(AttachmentData(name, media_type, data, digest, attachment_id))
        return loaded

    def delete_session(self, session_id: str) -> None:
        directory = self.session_directory(session_id)
        if directory.exists():
            shutil.rmtree(directory)

    def session_directory(self, session_id: str) -> Path:
        key = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        return self.root / key

    @staticmethod
    def _atomic_write(target: Path, data: bytes) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.chmod(temporary_name, 0o600)
            except OSError:
                pass
            os.replace(temporary_name, target)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)


def build_attachment_contents(
    attachments: Sequence[AttachmentData],
    api_type: ApiType | str,
) -> list[Content]:
    """Map validated files to MAF content using native inputs or safe text fallback."""
    if not attachments:
        return []
    resolved_api_type = api_type if isinstance(api_type, ApiType) else ApiType(api_type)
    fallback_count = sum(
        attachment.media_type == "text/plain"
        or (
            attachment.media_type == "application/pdf"
            and resolved_api_type != ApiType.RESPONSES
        )
        for attachment in attachments
    )
    fallback_remaining = fallback_count
    character_budget = MAX_EXTRACTED_TEXT_CHARACTERS
    contents: list[Content] = []

    for attachment in attachments:
        properties = {"filename": attachment.name}
        if attachment.media_type in {"image/jpeg", "image/png"}:
            contents.append(Content.from_text(text=f"Image attachment '{attachment.name}' follows."))
            contents.append(Content.from_data(
                attachment.data,
                attachment.media_type,
                additional_properties=properties,
            ))
            continue
        if attachment.media_type == "application/pdf" and resolved_api_type == ApiType.RESPONSES:
            contents.append(Content.from_data(
                attachment.data,
                attachment.media_type,
                additional_properties=properties,
            ))
            continue

        per_file_budget = max(1, character_budget // max(1, fallback_remaining))
        if attachment.media_type == "text/plain":
            extracted, truncated = _text_attachment(attachment, per_file_budget)
            source = "UTF-8 text"
        elif attachment.media_type == "application/pdf":
            extracted, truncated = _extract_pdf_text(attachment, per_file_budget)
            source = "PDF text extracted locally"
        else:  # pragma: no cover - all media types are validated before this point
            raise AttachmentError(f"Unsupported attachment media type: {attachment.media_type}")

        if not extracted.strip():
            raise AttachmentError(
                f"Attachment '{attachment.name}' contains no extractable text for "
                f"{resolved_api_type.value}. Use a Responses model for scanned or image-only PDFs."
            )
        used = min(len(extracted), per_file_budget)
        character_budget = max(0, character_budget - used)
        fallback_remaining -= 1
        marker = "\n[Attachment content was truncated by the client.]" if truncated else ""
        contents.append(Content.from_text(text=(
            f"[Begin attachment: {attachment.name} ({source})]\n"
            f"{extracted}{marker}\n"
            f"[End attachment: {attachment.name}]"
        )))
    return contents


def _text_attachment(attachment: AttachmentData, limit: int) -> tuple[str, bool]:
    try:
        text = attachment.data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:  # validation normally catches this first
        raise AttachmentError(f"Text attachment '{attachment.name}' must be UTF-8 encoded.") from exc
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def _extract_pdf_text(attachment: AttachmentData, limit: int) -> tuple[str, bool]:
    try:
        reader = PdfReader(io.BytesIO(attachment.data), strict=False)
        if reader.is_encrypted:
            raise AttachmentError(f"PDF attachment '{attachment.name}' must not be encrypted.")
        total_pages = len(reader.pages)
        parts: list[str] = []
        length = 0
        page_limit = min(total_pages, MAX_PDF_PAGES_TO_EXTRACT)
        truncated = total_pages > page_limit
        for index in range(page_limit):
            page_text = reader.pages[index].extract_text() or ""
            if page_text:
                page_text = f"\n[Page {index + 1}]\n{page_text}"
            remaining = limit - length
            if len(page_text) > remaining:
                parts.append(page_text[:remaining])
                truncated = True
                break
            parts.append(page_text)
            length += len(page_text)
            if length >= limit:
                truncated = index + 1 < total_pages
                break
        return "".join(parts).strip(), truncated
    except AttachmentError:
        raise
    except (PdfReadError, OSError, ValueError, TypeError, IndexError, KeyError) as exc:
        raise AttachmentError(f"PDF attachment '{attachment.name}' could not be read safely.") from exc


def _safe_filename(value: Any, index: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AttachmentError(f"Attachment {index} requires a filename.")
    name = re.split(r"[\\/]", value.strip())[-1]
    if not name or name in {".", ".."} or _CONTROL_CHARACTERS.search(name):
        raise AttachmentError(f"Attachment {index} has an invalid filename.")
    if len(name) > 255:
        raise AttachmentError(f"Attachment '{name[:40]}…' has a filename longer than 255 characters.")
    if Path(name).suffix.lower() not in _EXTENSION_MEDIA_TYPES:
        raise AttachmentError(
            f"Attachment '{name}' must be a PDF, TXT, JPEG/JPG, or PNG file."
        )
    return name


def _attachment_bytes(value: Any, index: int) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value)
    raise AttachmentError(f"Attachment {index} must contain binary data.")


def _canonical_media_type(name: str, value: Any) -> str:
    expected = _EXTENSION_MEDIA_TYPES[Path(name).suffix.lower()]
    if value is None:
        return expected
    if not isinstance(value, str):
        raise AttachmentError(f"Attachment '{name}' has invalid media type metadata.")
    supplied = value.split(";", 1)[0].strip().lower()
    supplied = _MEDIA_TYPE_ALIASES.get(supplied, supplied)
    if supplied in {None, ""}:
        return expected
    if supplied != expected:
        raise AttachmentError(
            f"Attachment '{name}' media type does not match its filename extension."
        )
    return expected


def _validate_signature(name: str, media_type: str, data: bytes) -> None:
    valid = True
    if media_type == "application/pdf":
        valid = data.startswith(b"%PDF-")
    elif media_type == "image/png":
        valid = data.startswith(b"\x89PNG\r\n\x1a\n")
    elif media_type == "image/jpeg":
        valid = data.startswith(b"\xff\xd8\xff")
    elif media_type == "text/plain":
        if b"\x00" in data:
            valid = False
        else:
            try:
                data.decode("utf-8-sig")
            except UnicodeDecodeError:
                valid = False
    if not valid:
        raise AttachmentError(f"Attachment '{name}' content does not match its supported file type.")


__all__ = [
    "AttachmentData",
    "AttachmentError",
    "AttachmentStore",
    "DEFAULT_ATTACHMENT_PROMPT",
    "MAX_ATTACHMENT_BYTES",
    "MAX_ATTACHMENT_COUNT",
    "MAX_EXTRACTED_TEXT_CHARACTERS",
    "MAX_TOTAL_ATTACHMENT_BYTES",
    "SOCKET_MAX_HTTP_BUFFER_BYTES",
    "SUPPORTED_ATTACHMENT_MEDIA_TYPES",
    "build_attachment_contents",
    "parse_incoming_attachments",
]
