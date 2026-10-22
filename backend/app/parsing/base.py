"""解析结果的统一形状 —— 「解析」与「评估」之间唯一的接口。

字段命名对齐 `data/docs/paper_structuring_spec.md` v0.1 与
`sql/001_initial_schema.sql`：chunk 有 `chunk_id`、跨页的 `page_start`/`page_end`
和解析器自评的 `location_confidence`；表/图/公式是独立的 element，不混在正文里。

任何解析器只要产出 ParsedDocument，分析层就能拼 prompt，校验层就能做证据回查。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from app.schemas.enums import Confidence, ParseQuality
from app.schemas.paper import Paper

_CONFIDENCE_RANK = {Confidence.LOW: 0, Confidence.MEDIUM: 1, Confidence.HIGH: 2}


def confidence_rank(c: Confidence) -> int:
    """置信度排序用。high > medium > low。"""
    return _CONFIDENCE_RANK[c]


# 「Table 1」「Figure 2」「Tables 4–6」「Eq. 3」都要能认出来。
_LABEL_RE = re.compile(
    r"\b(tables?|tab\.?|figures?|figs?\.?|equations?|eqs?\.?|theorems?|thms?\.?)\s*"
    r"(\d+(?:\s*[-–—]\s*\d+)?(?:\s*,\s*\d+)*)",
    re.IGNORECASE,
)
_KIND_ALIASES = {
    "table": "table", "tables": "table", "tab": "table", "tab.": "table",
    "figure": "figure", "figures": "figure", "fig": "figure", "figs": "figure",
    "fig.": "figure", "figs.": "figure",
    "equation": "equation", "equations": "equation", "eq": "equation",
    "eqs": "equation", "eq.": "equation", "eqs.": "equation",
    "theorem": "theorem", "theorems": "theorem", "thm": "theorem",
    "thms": "theorem", "thm.": "theorem",
}


def parse_label_tokens(label: str | None) -> set[tuple[str, int]]:
    """把 locator_label 拆成 (element_type, number) 集合。

    模型经常写「Tables 4–6」这种区间标签，逐字匹配一定失败，所以这里展开成
    {(table,4),(table,5),(table,6)}，只要命中一个就算这条证据能定位。
    认不出编号的自由标签返回空集 —— 交给调用方决定「无法核对」而不是「核对失败」。
    """
    tokens: set[tuple[str, int]] = set()
    if not label:
        return tokens
    for kind_raw, numbers_raw in _LABEL_RE.findall(label):
        kind = _KIND_ALIASES.get(kind_raw.lower())
        if kind is None:
            continue
        span = re.match(r"^\s*(\d+)\s*[-–—]\s*(\d+)\s*$", numbers_raw)
        if span:
            lo, hi = int(span.group(1)), int(span.group(2))
            if lo <= hi and hi - lo <= 50:  # 防御：别被 "Table 1-9999" 撑爆
                tokens.update((kind, n) for n in range(lo, hi + 1))
            continue
        tokens.update((kind, int(n)) for n in re.findall(r"\d+", numbers_raw))
    return tokens


@dataclass
class TextBlock:
    """一段带位置的正文（benchmark 的 text chunk）。

    `chunk_id` 是模型唯一被允许引用的正文位置标识；`location_confidence` 是
    解析器对这个位置的自评，证据置信度不得高于它。
    """

    text: str
    page_start: int
    page_end: int | None = None
    chunk_id: str = ""
    section_id: str = ""
    section_title: str = ""
    content_type: str = "body_text"
    location_confidence: Confidence = Confidence.HIGH
    # benchmark sections.json 的 section_role（abstract / introduction / references ...）。
    # 解析器给不出时为空串，调用方不得据此推断章节类型。
    section_role: str = ""

    @property
    def pages(self) -> set[int]:
        """这一块覆盖的全部页码。跨页 chunk 的中间页也是合法页码。"""
        end = self.page_end if self.page_end is not None else self.page_start
        if end < self.page_start:
            end = self.page_start
        return set(range(self.page_start, end + 1))


@dataclass
class PaperElement:
    """表 / 图 / 公式 / 定理。证据里的 `locator_label` 要能在这里查到。"""

    element_id: str
    element_type: str
    label: str | None = None
    caption: str | None = None
    page_number: int | None = None
    section_id: str | None = None
    extracted_text: str | None = None
    parse_quality: ParseQuality = ParseQuality.PARTIAL
    section_role: str = ""

    @property
    def label_tokens(self) -> set[tuple[str, int]]:
        return parse_label_tokens(self.label)


@dataclass
class PaperLink:
    """论文里印出来的链接。link_type 取值对齐结构化规范 §8。"""

    url: str
    link_type: str = "other"
    source_chunk_id: str | None = None


@dataclass
class SourceIndex:
    """证据回查需要的全部信息，一次性算好。

    校验层只依赖这个对象，不依赖具体解析器，因此 fixture、GROBID、benchmark 的
    benchmark 产物三条来源可以走同一套校验。
    """

    plain_text: str | None = None
    pages: set[int] = field(default_factory=set)
    chunk_ids: set[str] = field(default_factory=set)
    chunk_confidence: dict[str, Confidence] = field(default_factory=dict)
    element_pages: dict[tuple[str, int], int | None] = field(default_factory=dict)
    code_links: list[str] = field(default_factory=list)
    parse_quality: ParseQuality = ParseQuality.GOOD

    @property
    def has_elements(self) -> bool:
        return bool(self.element_pages)


@dataclass
class ParsedDocument:
    paper: Paper
    blocks: list[TextBlock] = field(default_factory=list)
    elements: list[PaperElement] = field(default_factory=list)
    links: list[PaperLink] = field(default_factory=list)
    parse_quality: ParseQuality = ParseQuality.GOOD
    warnings: list[str] = field(default_factory=list)
    source_sha256: str | None = None
    source_path: str | None = None
    parser_name: str = "unknown"
    source_categories: list[str] = field(default_factory=list)

    @property
    def known_pages(self) -> set[int]:
        """解析出的合法页码集合，用于拦截模型编造的 page_number。

        跨页 chunk 的中间页同样合法 —— 只取 page_start 会把真实页码误判成编造。
        """
        pages: set[int] = set()
        for b in self.blocks:
            pages |= b.pages
        pages |= {e.page_number for e in self.elements if e.page_number is not None}
        return pages

    @property
    def plain_text(self) -> str:
        """不带 anchor 的纯文本，用于 quote 回查与确定性交叉校验。"""
        return "\n\n".join(b.text for b in self.blocks)

    def build_index(self) -> SourceIndex:
        element_pages: dict[tuple[str, int], int | None] = {}
        for e in self.elements:
            for token in e.label_tokens:
                element_pages.setdefault(token, e.page_number)
        return SourceIndex(
            plain_text=self.plain_text,
            pages=self.known_pages,
            chunk_ids={b.chunk_id for b in self.blocks if b.chunk_id},
            chunk_confidence={b.chunk_id: b.location_confidence for b in self.blocks if b.chunk_id},
            element_pages=element_pages,
            code_links=[link.url for link in self.links if link.link_type == "code"],
            parse_quality=self.parse_quality,
        )


class Parser(Protocol):
    def parse_pdf(self, path: str) -> ParsedDocument: ...
    def parse_arxiv(self, arxiv_id: str) -> ParsedDocument: ...
