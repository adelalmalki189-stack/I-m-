"""Storing customer documents on disk (never served statically; see the download endpoints)."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.insurance.models import RequestDocument

PROJECT_ROOT = Path(__file__).resolve().parents[2]
UPLOAD_ROOT = PROJECT_ROOT / "app" / "static" / "uploads"
STAFF_MAX_UPLOAD_BYTES = 25 * 1024 * 1024
# Public uploads must really be PDF/JPEG/PNG — checked by the file's first bytes, not its name.
PDF_AND_IMAGES = {
    b"%PDF": "application/pdf",
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
}


class DocumentRejected(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code = status, code


async def store(
    session: AsyncSession,
    request_id: str,
    file: UploadFile,
    document_type: str,
    uploader_id: str | None,
    max_bytes: int,
    allowed: dict[bytes, str] | None = None,
    claim_id: str | None = None,
) -> RequestDocument:
    # Keep only the final path component so a crafted name like "../../x" can't escape the uploads folder.
    file_name = Path(file.filename or "").name
    if not file_name:
        raise DocumentRejected(400, "INVALID_FILE", "A file name is required")
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise DocumentRejected(413, "FILE_TOO_LARGE", f"Files must be at most {max_bytes // (1024 * 1024)} MB")
    mime_type = file.content_type or "application/octet-stream"
    if allowed is not None:
        detected = next((mime for magic, mime in allowed.items() if content.startswith(magic)), None)
        if detected is None:
            raise DocumentRejected(415, "UNSUPPORTED_FILE_TYPE", "Only PDF, JPEG and PNG files are accepted")
        mime_type = detected

    folder = UPLOAD_ROOT / request_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{uuid4().hex}-{file_name}"
    path.write_bytes(content)
    document = RequestDocument(
        request_id=request_id,
        claim_id=claim_id,
        document_type=document_type,
        file_name=file_name,
        mime_type=mime_type,
        size_bytes=len(content),
        storage_key=str(path.relative_to(PROJECT_ROOT)),
        uploaded_by_employee_id=uploader_id,
    )
    session.add(document)
    await session.flush()
    return document


def resolve_path(storage_key: str | None) -> Path | None:
    if not storage_key:
        return None
    candidate = Path(storage_key)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate if candidate.exists() else None
