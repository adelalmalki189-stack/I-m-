"""Storing customer documents on disk (never served statically; see the download endpoints)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.insurance import catalog, review
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
# Lists (census, fleet, assets) may also be Excel; review_file checks it really is a workbook.
WITH_SPREADSHEETS = {**PDF_AND_IMAGES, b"PK\x03\x04": review.XLSX_MIME}


class DocumentRejected(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code = status, code


def allowed_for(spec: catalog.Doc | None) -> dict[bytes, str]:
    return WITH_SPREADSHEETS if spec is None or spec.get("spreadsheet") else PDF_AND_IMAGES


def spec_for(line: str, product: str, customer_type: str, document_type: str) -> catalog.Doc | None:
    return next(
        (d for d in catalog.required_documents(line, product, customer_type) if d["key"] == document_type),
        None,
    )


async def store(
    session: AsyncSession,
    request_id: str,
    file: UploadFile,
    document_type: str,
    uploader_id: str | None,
    max_bytes: int,
    allowed: dict[bytes, str] | None = None,
    claim_id: str | None = None,
    spec: catalog.Doc | None = None,
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
    return await store_bytes(
        session, request_id, content, file_name, mime_type, document_type, uploader_id, claim_id, spec
    )


async def store_bytes(
    session: AsyncSession,
    request_id: str,
    content: bytes,
    file_name: str,
    mime_type: str,
    document_type: str,
    uploader_id: str | None = None,
    claim_id: str | None = None,
    spec: catalog.Doc | None = None,
    reviewed: bool = True,
) -> RequestDocument:
    sha256 = hashlib.sha256(content).hexdigest()
    status, findings = review.PASSED, list[str]()
    if reviewed and claim_id is None:
        duplicate = (
            await session.execute(
                select(RequestDocument.document_type).where(
                    RequestDocument.request_id == request_id, RequestDocument.content_sha256 == sha256
                )
            )
        ).scalar()
        status, findings = review.review_file(
            content,
            mime_type,
            document_type,
            spec,
            catalog.document_label(duplicate) if duplicate else None,
        )

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
        content_sha256=sha256,
        review_status=status,
        review_findings=json.dumps(findings, ensure_ascii=False) if findings else None,
    )
    session.add(document)
    await session.flush()
    return document


def findings_of(document: RequestDocument) -> list[str]:
    try:
        return list(json.loads(document.review_findings or "[]"))
    except json.JSONDecodeError:
        return []


def resolve_path(storage_key: str | None) -> Path | None:
    if not storage_key:
        return None
    candidate = Path(storage_key)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate if candidate.exists() else None
