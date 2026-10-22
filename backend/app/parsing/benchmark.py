"""读取 benchmark 数据目录里的解析产物。

对应 `data/data/parsed/<paper_id>/<version>/`：

    metadata.json     paper_version 级元信息与解析质量
    sections.json     章节树
    chunks.jsonl      正文分块，一行一个 chunk
    elements.json     表 / 图 / 公式 / 定理
    parse_report.json 验收闸门结果（可选）

用途有两个：
1. 联调时不必等 GROBID 通道，直接拿 benchmark 里已经人工核过的真实解析
   跑完整的校验链路；
2. 把 benchmark 的字段形状固化成代码，两边对不上时是这里报错，而不是到线上才发现。

注意：这个 loader 只读文件，**不做质量判断**。`parse_quality: poor` 的处理在
app.validation.report_validator（契约 §17.2 + 结构化规范的验收闸门）。
"""
from __future__ import annotations

import json
from pathlib import Path

from app.parsing.base import PaperElement, PaperLink, ParsedDocument, TextBlock
from app.schemas.enums import Confidence, ParseQuality
from app.schemas.paper import Paper, Section


class BenchmarkParseError(ValueError):
    """benchmark 产物缺文件或字段形状不对。宁可直接报错，也不要静默降级。"""


class BenchmarkSourceMismatch(BenchmarkParseError):
    """请求的论文与当前唯一允许的 benchmark 样本不一致。"""


_REQUIRED = ("metadata.json", "sections.json", "chunks.jsonl")


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BenchmarkParseError(f"缺少文件：{path}") from exc
    except json.JSONDecodeError as exc:
        raise BenchmarkParseError(f"{path.name} 不是合法 JSON：{exc}") from exc


def _confidence(raw: object, default: Confidence = Confidence.MEDIUM) -> Confidence:
    try:
        return Confidence(str(raw))
    except ValueError:
        return default


def _parse_quality(raw: object, default: ParseQuality = ParseQuality.PARTIAL) -> ParseQuality:
    try:
        return ParseQuality(str(raw))
    except ValueError:
        return default


def load_parsed_document(directory: str | Path) -> ParsedDocument:
    """把一个版本目录读成 ParsedDocument。"""
    root = Path(directory)
    if not root.is_dir():
        raise BenchmarkParseError(f"不是目录：{root}")
    missing = [name for name in _REQUIRED if not (root / name).is_file()]
    if missing:
        raise BenchmarkParseError(f"{root} 缺少 {', '.join(missing)}")

    meta = _read_json(root / "metadata.json")
    if not isinstance(meta, dict):
        raise BenchmarkParseError("metadata.json 顶层必须是对象")

    sections_raw = _read_json(root / "sections.json")
    if not isinstance(sections_raw, list):
        raise BenchmarkParseError("sections.json 顶层必须是数组")

    section_titles: dict[str, str] = {}
    section_roles: dict[str, str] = {}
    sections: list[Section] = []
    for item in sections_raw:
        if not isinstance(item, dict):
            continue
        section_id = str(item.get("section_id", ""))
        title = str(item.get("title") or "")
        section_titles[section_id] = title
        section_roles[section_id] = str(item.get("section_role") or "")
        sections.append(
            Section(
                section_id=section_id,
                title=title,
                level=int(item.get("level") or 0),
                page_start=item.get("page_start"),
                page_end=item.get("page_end"),
            )
        )

    blocks: list[TextBlock] = []
    with (root / "chunks.jsonl").open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError as exc:
                raise BenchmarkParseError(f"chunks.jsonl 第 {lineno} 行不是合法 JSON：{exc}") from exc
            page_start = chunk.get("page_start")
            if page_start is None:
                raise BenchmarkParseError(f"chunks.jsonl 第 {lineno} 行缺 page_start，证据无法定位")
            section_id = str(chunk.get("section_id") or "")
            blocks.append(
                TextBlock(
                    text=str(chunk.get("text_content") or ""),
                    page_start=int(page_start),
                    page_end=chunk.get("page_end"),
                    chunk_id=str(chunk.get("chunk_id") or ""),
                    section_id=section_id,
                    section_title=section_titles.get(section_id, ""),
                    content_type=str(chunk.get("content_type") or "body_text"),
                    location_confidence=_confidence(chunk.get("location_confidence")),
                    section_role=section_roles.get(section_id, ""),
                )
            )
    blocks.sort(key=lambda b: (b.page_start, b.chunk_id))

    elements: list[PaperElement] = []
    elements_path = root / "elements.json"
    if elements_path.is_file():
        elements_raw = _read_json(elements_path)
        if not isinstance(elements_raw, list):
            raise BenchmarkParseError("elements.json 顶层必须是数组")
        for item in elements_raw:
            if not isinstance(item, dict):
                continue
            elements.append(
                PaperElement(
                    element_id=str(item.get("element_id", "")),
                    element_type=str(item.get("element_type", "")),
                    label=item.get("label"),
                    caption=item.get("caption"),
                    page_number=item.get("page_number"),
                    section_id=item.get("section_id"),
                    extracted_text=item.get("extracted_text"),
                    parse_quality=_parse_quality(item.get("parse_quality")),
                    section_role=section_roles.get(str(item.get("section_id") or ""), ""),
                )
            )

    links: list[PaperLink] = []
    links_path = root / "links.json"
    if links_path.is_file():
        links_raw = _read_json(links_path)
        if isinstance(links_raw, list):
            for item in links_raw:
                if isinstance(item, dict) and item.get("url"):
                    links.append(
                        PaperLink(
                            url=str(item["url"]),
                            link_type=str(item.get("link_type") or "other"),
                            source_chunk_id=item.get("source_chunk_id"),
                        )
                    )

    warnings = [str(w) for w in (meta.get("parse_warnings") or [])]
    abstract = next((b.text for b in blocks if b.section_id.endswith("abstract")), None)

    paper = Paper(
        paper_id=str(meta.get("paper_id") or root.name),
        title=meta.get("title"),
        authors=[str(a) for a in (meta.get("authors") or [])],
        abstract=meta.get("abstract") or abstract,
        language=meta.get("language"),
        page_count=meta.get("page_count"),
        arxiv_id=meta.get("arxiv_id"),
        doi=meta.get("doi"),
        sections=sections,
        parse_quality=_parse_quality(meta.get("parse_quality")),
        parse_warnings=warnings,
    )
    return ParsedDocument(
        paper=paper,
        blocks=blocks,
        elements=elements,
        links=links,
        parse_quality=paper.parse_quality,
        warnings=warnings,
    )


class BenchmarkParser:
    """读取一份经过 benchmark 检查的结构化论文，仅用于受限联调。

    这个解析器不会把任意输入悄悄映射到固定样本。调用方必须提交与
    ``expected_arxiv_id`` 完全一致的 arXiv 版本；PDF 输入暂不支持。
    """

    def __init__(
        self,
        directory: str | Path,
        expected_arxiv_id: str,
        source_record: str | Path | None = None,
    ) -> None:
        self.directory = Path(directory)
        self.expected_arxiv_id = expected_arxiv_id.strip()
        self.source_record = Path(source_record) if source_record else None

    def parse_pdf(self, path: str) -> ParsedDocument:
        raise BenchmarkSourceMismatch(
            "benchmark 模式仅支持样本 2307.08122v3，不支持任意 PDF 上传"
        )

    def parse_arxiv(self, arxiv_id: str) -> ParsedDocument:
        requested = arxiv_id.strip()
        if requested != self.expected_arxiv_id:
            raise BenchmarkSourceMismatch(
                f"benchmark 模式仅支持 {self.expected_arxiv_id}，收到 {requested or '<empty>'}"
            )

        doc = load_parsed_document(self.directory)
        self._merge_source_record(doc, requested)
        return doc

    def _merge_source_record(self, doc: ParsedDocument, requested_arxiv_id: str) -> None:
        """补齐解析 metadata 暂缺、但来源快照已经核实的论文身份字段。"""
        data: dict = {}
        if self.source_record is not None:
            raw = _read_json(self.source_record)
            if not isinstance(raw, dict):
                raise BenchmarkParseError("source_record 顶层必须是对象")
            data = raw
            record_paper_id = str(data.get("paper_id") or "")
            if record_paper_id and record_paper_id != doc.paper.paper_id:
                raise BenchmarkParseError(
                    f"source_record paper_id={record_paper_id} 与解析产物 {doc.paper.paper_id} 不一致"
                )

            base_id = str(data.get("arxiv_id") or "")
            version = str(data.get("arxiv_version") or "")
            recorded_id = f"{base_id}{version}" if base_id else ""
            if recorded_id and recorded_id != requested_arxiv_id:
                raise BenchmarkSourceMismatch(
                    f"source_record 对应 {recorded_id}，与请求 {requested_arxiv_id} 不一致"
                )

        paper_data = doc.paper.model_dump()
        paper_data.update(
            title=data.get("title") or doc.paper.title,
            authors=data.get("authors") or doc.paper.authors,
            language=data.get("language") or doc.paper.language,
            arxiv_id=requested_arxiv_id,
        )
        doc.paper = Paper.model_validate(paper_data)
