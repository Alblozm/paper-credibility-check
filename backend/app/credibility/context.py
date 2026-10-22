"""把可信度请求落到一份已解析的论文版本上，三项检查共用同一个 CheckContext。

解析复用 app.parsing.benchmark.load_parsed_document，不另读一套 JSON。
优先复用 benchmark 数据里已解析的版本：
- arXiv：按 source_record 的 arxiv_id 匹配。带版本号时目录名必须是该版本；
  不带版本号时取 source_record 记录的版本。版本对不上就不解析，不拿别的版本顶替。
- PDF：经 app.storage.uploads.resolve_upload 取上传文件，按 sha256 匹配 pdf_sha256。
不在 benchmark 里的输入，只有 parser_provider 为 generic / grobid 时才交给
pipeline.orchestrator.build_parser 现场解析；stub 解析器是假文档，不用于核查。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import HTTPException

from app.config import get_settings
from app.credibility.models import CredibilityPaper
from app.parsing.arxiv import ArxivDownloadError, ArxivSourceError
from app.parsing.base import PaperElement, ParsedDocument, TextBlock
from app.parsing.benchmark import BenchmarkParseError, load_parsed_document
from app.parsing.generic import PdfParseError
from app.storage.uploads import resolve_upload

logger = logging.getLogger(__name__)
_LIVE_PROVIDERS = {"generic", "grobid"}

BENCHMARK_DATA = Path("benchmark/data")
_BENCHMARK_LAYOUTS = (Path("data/data"), BENCHMARK_DATA)
_ARXIV_RE = re.compile(r"^(\d{4}\.\d{4,5})(v\d+)?$")
_ARXIV_PREFIX = re.compile(r"^(?:arxiv:|https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/)", re.IGNORECASE)
_PARSE_QUALITY = {"good", "partial", "poor"}


class ParseFailure(str):
    """An unresolved-paper reason that also carries the API error code."""

    code: str
    retryable: bool

    def __new__(cls, message: str, *, code: str = "PDF_PARSE_FAILED", retryable: bool = False) -> ParseFailure:
        value = super().__new__(cls, message)
        value.code = code
        value.retryable = retryable
        return value


def resolve_repo_root(repo_root: Path | None = None) -> Path:
    """仓库根目录；测试可传入临时目录替换 benchmark 数据文件。"""
    if repo_root is None:
        return Path(__file__).resolve().parents[3]
    return Path(repo_root)


@dataclass(frozen=True)
class ParsedPaperRef:
    paper_id: str
    paper_version_id: str
    version_label: str
    parsed_dir: Path
    metadata: dict
    source_record: dict


@dataclass
class CheckContext:
    """三项检查的共同输入。检查函数只读，不改 doc。"""

    source: dict
    repo_root: Path
    generated_at: str
    ref: ParsedPaperRef | None
    doc: ParsedDocument | None
    unresolved_reason: str | None
    model_id: str | None = None
    assessment_id: str | None = None
    checkpoint_load: Callable[[str, str], dict | None] | None = None
    checkpoint_save: Callable[[str, str, dict], None] | None = None
    previous_check: dict | None = None
    model_history: list[dict] = field(default_factory=list)

    @property
    def paper_version_id(self) -> str | None:
        if self.ref is not None:
            return self.ref.paper_version_id
        if self.doc is None:
            return None
        # A live parse must never borrow a benchmark version id. The PDF digest
        # distinguishes revisions even when an uploaded paper reuses a title/id.
        arxiv = next(
            (
                match.group(0).lower()
                for raw in (self.doc.paper.arxiv_id, self.source.get("arxiv_id"))
                if isinstance(raw, str)
                and (match := _ARXIV_RE.fullmatch(_ARXIV_PREFIX.sub("", raw.strip()).lower().removesuffix(".pdf")))
                and match.group(2)
            ),
            None,
        )
        identity = f"arxiv:{arxiv}" if arxiv else self.doc.paper.paper_id
        digest = _text(self.doc.source_sha256)
        if digest is None:
            # Parsers without a source hash still get a deterministic identity;
            # do not use file paths, generation time or a process-random hash.
            payload = {
                "paper_id": self.doc.paper.paper_id,
                "arxiv_id": self.doc.paper.arxiv_id,
                "title": self.doc.paper.title,
                "blocks": [(b.chunk_id, b.page_start, b.page_end, b.text) for b in self.doc.blocks],
                "elements": [
                    (e.element_id, e.page_number, e.caption, e.extracted_text) for e in self.doc.elements
                ],
            }
            digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return f"live:{identity}:sha256:{digest.lower()}"

    def block(self, chunk_id: str) -> TextBlock | None:
        if self.doc is None:
            return None
        return next((b for b in self.doc.blocks if b.chunk_id == chunk_id), None)

    def element(self, element_id: str) -> PaperElement | None:
        if self.doc is None:
            return None
        return next((e for e in self.doc.elements if e.element_id == element_id), None)

    def blocks_with_role(self, *roles: str) -> list[TextBlock]:
        """按 benchmark section_role 取正文块。引用核查取 references 时需自行判断该角色是否可靠。"""
        if self.doc is None:
            return []
        wanted = set(roles)
        return [b for b in self.doc.blocks if b.section_role in wanted]

    def section_title(self, section_id: str | None) -> str | None:
        if self.doc is None or not section_id:
            return None
        title = next((s.title for s in self.doc.paper.sections if s.section_id == section_id), None)
        return title or None


def build_context(source: dict, *, generated_at: str, repo_root: Path | None = None) -> CheckContext:
    root = resolve_repo_root(repo_root)
    if get_settings().credibility_parse_source == "benchmark":
        ref, reason = _resolve(source, root)
    else:
        ref, reason = None, "Backend live parsing is configured (credibility_parse_source=live)."
    if ref is not None:
        doc = load_parsed_document(ref.parsed_dir)
    else:
        doc, reason = _parse_live(source, reason)
    return CheckContext(
        source=source,
        repo_root=root,
        generated_at=generated_at,
        ref=ref,
        doc=doc,
        unresolved_reason=reason,
    )


def paper_from_context(ctx: CheckContext) -> CredibilityPaper | None:
    """论文标识只取 benchmark 文件或现场解析结果；读不到的字段留 null。"""
    if ctx.ref is None:
        if ctx.doc is None:
            return None
        paper = ctx.doc.paper
        return CredibilityPaper(
            paper_id=paper.paper_id,
            title=paper.title,
            arxiv_id=paper.arxiv_id,
            source_sha256=ctx.doc.source_sha256,
            page_count=paper.page_count,
            language=paper.language,
            parse_quality=ctx.doc.parse_quality.value,
        )
    meta = ctx.ref.metadata
    record = ctx.ref.source_record
    quality = meta.get("parse_quality")
    return CredibilityPaper(
        paper_id=ctx.ref.paper_id,
        title=_text(record.get("title")),
        arxiv_id=_arxiv_with_version(ctx.ref),
        source_sha256=_text(meta.get("pdf_sha256")),
        page_count=meta.get("page_count") if isinstance(meta.get("page_count"), int) else None,
        language=_text(record.get("language")),
        parse_quality=quality if quality in _PARSE_QUALITY else "partial",
    )


def parser_version(ctx: CheckContext) -> str:
    if ctx.ref is None:
        return f"live:{ctx.doc.parser_name}" if ctx.doc is not None else "not_run"
    meta = ctx.ref.metadata
    name, version = _text(meta.get("parser_name")), _text(meta.get("parser_version"))
    if name and version:
        return f"benchmark:{name}@{version}"
    return "benchmark:unknown"


def _resolve(source: dict, root: Path) -> tuple[ParsedPaperRef | None, str | None]:
    # Keep discovery inside the supplied root. In particular, a test/private
    # repo_root must not silently fall back to the developer's production data.
    directories = [root / layout for layout in _BENCHMARK_LAYOUTS if (root / layout / "parsed").is_dir()]
    if not directories:
        return None, "The parsed benchmark directory is not available on this server."
    if source.get("type") not in {"arxiv", "pdf"}:
        return None, "Unsupported source type."
    reason = None
    for data in directories:
        if source.get("type") == "arxiv":
            ref, reason = _resolve_arxiv(str(source.get("arxiv_id") or ""), data)
        else:
            ref, reason = _resolve_pdf(str(source.get("upload_id") or ""), data)
        if ref is not None:
            return ref, None
    return None, reason


def _resolve_arxiv(raw: str, data: Path) -> tuple[ParsedPaperRef | None, str | None]:
    value = _ARXIV_PREFIX.sub("", raw.strip()).removesuffix(".pdf").lower()
    match = _ARXIV_RE.fullmatch(value)
    if match is None:
        return None, f"'{raw}' is not a recognised arXiv identifier."
    base, version = match.group(1), match.group(2)
    for ref in _iter_refs(data):
        if _text(ref.source_record.get("arxiv_id")) != base:
            continue
        wanted = version or _text(ref.source_record.get("arxiv_version"))
        if wanted and ref.version_label == wanted:
            return ref, None
    label = f"{base}{version or ''}"
    return None, f"arXiv {label} is not in the parsed benchmark."


def _resolve_pdf(upload_id: str, data: Path) -> tuple[ParsedPaperRef | None, str | None]:
    try:
        path = resolve_upload(upload_id)
    except HTTPException:
        return None, "The uploaded PDF was not found. It may have been cleared when the service restarted."
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    for ref in _iter_refs(data):
        if ref.metadata.get("pdf_sha256") == digest:
            return ref, None
    return None, "The uploaded PDF does not match a paper in the parsed benchmark."


def _parse_live(source: dict, reason: str | None) -> tuple[ParsedDocument | None, str | None]:
    provider = get_settings().parser_provider
    if provider not in _LIVE_PROVIDERS:
        return None, f"{reason} Live parsing is not enabled on this server (parser_provider={provider})."
    from app.pipeline.orchestrator import build_parser

    parser = build_parser()
    try:
        if source.get("type") == "arxiv":
            raw = str(source.get("arxiv_id") or "").strip()
            return parser.parse_arxiv(_ARXIV_PREFIX.sub("", raw).removesuffix(".pdf")), None
        return parser.parse_pdf(str(resolve_upload(str(source.get("upload_id") or "")))), None
    except HTTPException:
        return None, ParseFailure(
            "The uploaded PDF was not found. It may have been cleared when the service restarted.",
            code="UPLOAD_NOT_FOUND",
        )
    except ArxivSourceError as exc:
        return None, ParseFailure(str(exc), code="INVALID_ARXIV_ID")
    except (ArxivDownloadError, PdfParseError) as exc:
        logger.warning("live parse failed for %s: %s", source, exc)
        return None, ParseFailure(str(exc), code=exc.code, retryable=getattr(exc, "retryable", False))
    except BenchmarkParseError:
        logger.warning("live parse failed for %s", source, exc_info=True)
        return None, ParseFailure("Live parsing could not produce a trustworthy structured paper.")


def _iter_refs(data: Path):
    for meta_path in sorted((data / "parsed").glob("*/*/metadata.json")):
        meta = _read_object(meta_path)
        if meta is None:
            continue
        paper_id = _text(meta.get("paper_id")) or meta_path.parents[1].name
        record = _read_object(data / "source_snapshots" / paper_id / "source_record.json") or {}
        version_label = meta_path.parent.name
        yield ParsedPaperRef(
            paper_id=paper_id,
            paper_version_id=_text(meta.get("paper_version_id")) or f"{paper_id}_{version_label}",
            version_label=version_label,
            parsed_dir=meta_path.parent,
            metadata=meta,
            source_record=record,
        )


def _arxiv_with_version(ref: ParsedPaperRef) -> str | None:
    """只有解析版本就是 arXiv 版本时才给 arxiv_id，避免前端打开另一份 PDF 的页码。"""
    base = _text(ref.source_record.get("arxiv_id"))
    if base and re.fullmatch(r"v\d+", ref.version_label):
        return f"{base}{ref.version_label}"
    return None


def _read_object(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
