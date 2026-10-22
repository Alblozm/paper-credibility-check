"""Private local PDF storage for the single-instance integration environment."""
from __future__ import annotations

import hashlib
import io
import os
import re
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile
from pypdf import PdfReader

from app.config import get_settings


def reject(status: int, code: str, message: str) -> None:
    raise HTTPException(status_code=status, detail={"code": code, "message": message})


def resolve_upload(upload_id: str) -> Path:
    if not re.fullmatch(r"upl_[0-9a-f]{32}", upload_id):
        reject(404, "UPLOAD_NOT_FOUND", "The upload was not found. Please upload the PDF again.")
    path = Path(get_settings().upload_dir) / f"{upload_id}.pdf"
    if not path.is_file():
        reject(404, "UPLOAD_NOT_FOUND", "The upload was not found or has expired. Please upload the PDF again.")
    return path


async def save_upload(file: UploadFile) -> dict:
    settings = get_settings()
    try:
        if not (file.filename or "").lower().endswith(".pdf"):
            reject(400, "UNSUPPORTED_FILE_TYPE", "Select a PDF file.")
        limit = settings.max_upload_mb * 1024 * 1024
        content = bytearray()
        while chunk := await file.read(1024 * 1024):
            content.extend(chunk)
            if len(content) > limit:
                reject(413, "FILE_TOO_LARGE", f"The PDF must not exceed {settings.max_upload_mb} MB.")
        if not content:
            reject(400, "EMPTY_FILE", "The PDF file is empty. Select another file.")
        if not content.startswith(b"%PDF-"):
            reject(400, "INVALID_PDF", "The file is not a valid PDF.")
        try:
            pdf = PdfReader(io.BytesIO(content))
            if pdf.is_encrypted:
                reject(400, "PDF_ENCRYPTED", "Encrypted PDFs are not supported. Upload an unencrypted version.")
            pages = len(pdf.pages)
        except HTTPException:
            raise
        except Exception:
            reject(400, "INVALID_PDF", "The PDF could not be read. The file may be damaged.")
        if pages == 0:
            reject(400, "INVALID_PDF", "The PDF has no readable pages.")
        if pages > settings.max_page_count:
            reject(413, "PAGE_LIMIT_EXCEEDED", f"The PDF has {pages} pages; the limit is {settings.max_page_count}.")
        root = Path(settings.upload_dir)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        upload_id = f"upl_{uuid.uuid4().hex}"
        path = root / f"{upload_id}.pdf"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
        return {
            "upload_id": upload_id,
            "original_filename": Path(file.filename or "paper.pdf").name,
            "page_count": pages,
            "sha256": hashlib.sha256(content).hexdigest(),
        }
    finally:
        await file.close()
