"""Shared, dependency-light helpers for the manifest-driven pipeline."""
from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import certifi

ROOT = Path(__file__).resolve().parents[1]
ID_RE = re.compile(r"^paper_[0-9]{3,}$")
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")


def release_metadata() -> dict:
    """Load the single authoritative set of dataset/schema/rubric versions."""
    return json.loads((ROOT / "data/manifests/release.json").read_text(encoding="utf-8"))


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def load_pipeline_manifest(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    papers = raw.get("papers")
    if not isinstance(papers, list):
        raise ValueError("manifest.papers must be a JSON array")
    seen = set()
    for index, paper in enumerate(papers, 1):
        for key in ("paper_id", "version", "source_type", "source_url"):
            if not str(paper.get(key, "")).strip():
                raise ValueError(f"paper #{index}: missing {key}")
        pid, version = paper["paper_id"], paper["version"]
        if not ID_RE.fullmatch(pid):
            raise ValueError(f"paper #{index}: invalid paper_id {pid!r}")
        if not VERSION_RE.fullmatch(version):
            raise ValueError(f"{pid}: unsafe version {version!r}")
        if pid in seen:
            raise ValueError(f"duplicate paper_id: {pid}")
        if not paper["source_url"].startswith(("https://", "http://")):
            raise ValueError(f"{pid}: source_url must be HTTP(S)")
        seen.add(pid)
    return [p for p in papers if p.get("enabled", True)]


def download_pdf(url: str, destination: Path, timeout: int = 120, attempts: int = 3) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    last = None
    for attempt in range(1, attempts + 1):
        request = Request(url, headers={"User-Agent": "paper-credibility-check-data/1.0"})
        fd, name = tempfile.mkstemp(prefix="download-", suffix=".pdf", dir=destination.parent)
        try:
            tls_context = ssl.create_default_context(cafile=certifi.where())
            with urlopen(request, timeout=timeout, context=tls_context) as response, os.fdopen(fd, "wb") as output:
                content_type = response.headers.get("Content-Type", "")
                while True:
                    block = response.read(1024 * 1024)
                    if not block: break
                    output.write(block)
            candidate = Path(name)
            with candidate.open("rb") as handle: signature = handle.read(5)
            if candidate.stat().st_size < 1024 or signature != b"%PDF-":
                raise ValueError(f"download is not a valid PDF (Content-Type={content_type!r})")
            os.replace(candidate, destination)
            return
        except Exception as exc:
            last = exc
            if attempt < attempts: time.sleep(2 ** (attempt - 1))
        finally:
            if os.path.exists(name): os.unlink(name)
    raise RuntimeError(f"download failed after {attempts} attempts: {last}")
