from __future__ import annotations

import io
from pathlib import Path

import pytest
from pypdf import PdfWriter

from app.attachments import (
    AttachmentData,
    AttachmentError,
    AttachmentStore,
    MAX_ATTACHMENT_BYTES,
    build_attachment_contents,
    parse_incoming_attachments,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"png-bytes"
JPEG = b"\xff\xd8\xff\xe0" + b"jpeg-bytes"


def pdf_bytes(text: str = "") -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    if text:
        writer.add_metadata({"/Title": text})
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


def test_parse_incoming_attachments_validates_and_canonicalizes_binary_payloads():
    attachments = parse_incoming_attachments([
        {"name": "diagram.PNG", "mediaType": "image/png", "size": len(PNG), "data": PNG},
        {"name": "notes.txt", "mediaType": "text/plain;charset=utf-8", "size": 5, "data": b"hello"},
    ])

    assert [(item.name, item.media_type, item.size) for item in attachments] == [
        ("diagram.PNG", "image/png", len(PNG)),
        ("notes.txt", "text/plain", 5),
    ]
    assert all(len(item.sha256) == 64 for item in attachments)


@pytest.mark.parametrize(
    "payload, message",
    [
        ([{"name": "malware.png", "mediaType": "image/png", "size": 5, "data": b"hello"}], "content does not match"),
        ([{"name": "notes.txt", "mediaType": "text/plain", "size": 3, "data": b"\xff\xfe\x00"}], "content does not match"),
        ([{"name": "doc.pdf", "mediaType": "text/plain", "size": 5, "data": b"hello"}], "does not match its filename"),
        ([{"name": "archive.zip", "mediaType": "application/zip", "size": 5, "data": b"hello"}], "must be a PDF"),
        ([{"name": "too-large.txt", "mediaType": "text/plain", "size": MAX_ATTACHMENT_BYTES + 1, "data": b"x" * (MAX_ATTACHMENT_BYTES + 1)}], "10 MB"),
    ],
)
def test_parse_incoming_attachments_rejects_invalid_files(payload, message):
    with pytest.raises(AttachmentError, match=message):
        parse_incoming_attachments(payload)


def test_attachment_store_uses_content_addressing_and_checks_integrity(tmp_path: Path):
    store = AttachmentStore(tmp_path)
    attachment = AttachmentData("photo.jpg", "image/jpeg", JPEG, "73f2e64ddc8594d36b251ccefb40e86cfcd8eb52fdd4a5f0edc9c4c806848d63")
    # Use the actual digest for a valid record.
    import hashlib
    attachment = AttachmentData(
        attachment.name,
        attachment.media_type,
        attachment.data,
        hashlib.sha256(attachment.data).hexdigest(),
    )

    first = store.store("session/unsafe", [attachment])
    second = store.store("session/unsafe", [attachment])

    assert first[0]["id"] != second[0]["id"]
    assert first[0]["filename"] == "photo.jpg"
    assert first[0]["sizeBytes"] == len(JPEG)
    assert first[0]["contentHash"] == attachment.sha256
    directory = store.session_directory("session/unsafe")
    assert [path.name for path in directory.iterdir()] == [attachment.sha256]
    loaded = store.load("session/unsafe", first)
    assert loaded[0].data == JPEG
    (directory / attachment.sha256).write_bytes(JPEG + b"tampered")
    with pytest.raises(AttachmentError, match="integrity"):
        store.load("session/unsafe", first)

    store.delete_session("session/unsafe")
    assert not directory.exists()


def test_build_attachment_contents_uses_native_responses_inputs_and_text_fallback():
    pdf = AttachmentData("report.pdf", "application/pdf", pdf_bytes(), "0" * 64)
    text = AttachmentData("notes.txt", "text/plain", b"hello world", "1" * 64)
    image = AttachmentData("photo.jpg", "image/jpeg", JPEG, "2" * 64)

    response_contents = build_attachment_contents([pdf, text, image], "responses")
    assert [item.type for item in response_contents] == ["data", "text", "text", "data"]
    assert response_contents[0].media_type == "application/pdf"
    assert response_contents[0].additional_properties["filename"] == "report.pdf"
    assert "hello world" in response_contents[1].text
    assert response_contents[-1].media_type == "image/jpeg"


def test_pdf_without_extractable_text_fails_for_non_responses_api():
    pdf = AttachmentData("scan.pdf", "application/pdf", pdf_bytes(), "0" * 64)

    with pytest.raises(AttachmentError, match="no extractable text"):
        build_attachment_contents([pdf], "chat_completions")
