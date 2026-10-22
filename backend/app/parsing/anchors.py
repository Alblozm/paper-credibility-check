"""送进模型的位置 anchor 约定 —— 跨组接口。

问题：契约 §15 要求每条证据带 page_number / section_id 且不许编造，但模型看到的
是拼接后的纯文本，它天然不知道自己读到第几页。

解法：把位置信息以 anchor 的形式写进文本，prompt 里要求「只能引用你见过的
anchor」，后端再用 app.validation.evidence 回查。

格式（v0.2，对齐 `paper_structuring_spec.md`）：

    [p1-2|paper_001_v3_chunk_000] We introduce Tangent Attention Fine-Tuning ...

- 页码写成 `p1` 或跨页的 `p1-2`，直接来自 chunk 的 page_start / page_end
- 竖线后是 **chunk_id 原文**，模型把它填进 evidence 的 locator_label，
  后端就能反查到具体是哪一块，连带拿到解析器自评的 location_confidence

v0.1 曾假设成 `[p7|sec_04]`，即 anchor 里放 section_id。benchmark 实际产出的
section_id 形如 `paper_001_v3_sec_3_1`（带 paper_version 前缀），且真正稳定的
定位单元是 chunk 而不是 section，故改为 chunk_id。
"""
from __future__ import annotations

import re

from app.parsing.base import ParsedDocument, TextBlock
from app.schemas.enums import Confidence

# chunk_id 可能含字母、数字、下划线、连字符、点，但不含空格和竖线。
ANCHOR_RE = re.compile(r"\[p(\d+)(?:-(\d+))?\|([^\s\]|]+)\]")


def format_anchor(page_start: int, chunk_id: str, page_end: int | None = None) -> str:
    if page_end is not None and page_end > page_start:
        return f"[p{page_start}-{page_end}|{chunk_id}]"
    return f"[p{page_start}|{chunk_id}]"


def build_anchored_text(doc: ParsedDocument) -> str:
    """生成送进模型的带 anchor 正文。"""
    parts: list[str] = []
    for i, b in enumerate(doc.blocks):
        chunk_id = b.chunk_id or f"chunk_{i:03d}"
        parts.append(f"{format_anchor(b.page_start, chunk_id, b.page_end)} {b.text}")
    return "\n\n".join(parts)


def build_element_catalog(doc: ParsedDocument) -> str:
    """表 / 图 / 公式清单，单独列给模型。

    正文 anchor 里没有表格编号，不给清单模型就只能凭空写「Table 3」。列出来之后
    「只能引用见过的编号」这条约束才有意义。
    """
    lines: list[str] = []
    for e in doc.elements:
        page = f"p{e.page_number}" if e.page_number is not None else "p?"
        caption = (e.caption or "").strip()
        if len(caption) > 200:
            caption = caption[:197] + "..."
        lines.append(f"- [{page}] {e.label or e.element_id}: {caption}")
    return "\n".join(lines)


def collect_anchors(anchored_text: str) -> set[tuple[int, str]]:
    """取出文本里出现过的全部 (page_start, chunk_id)。"""
    return {(int(p1), chunk) for p1, _p2, chunk in ANCHOR_RE.findall(anchored_text)}


def strip_anchors(anchored_text: str) -> str:
    """去掉 anchor，得到可用于 quote 逐字回查的纯文本。"""
    return ANCHOR_RE.sub("", anchored_text)


def blocks_from_anchored_text(anchored_text: str) -> list[TextBlock]:
    """把带 anchor 的文本还原成块。主要给测试和调试用。"""
    blocks: list[TextBlock] = []
    matches = list(ANCHOR_RE.finditer(anchored_text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(anchored_text)
        page_start = int(m.group(1))
        page_end = int(m.group(2)) if m.group(2) else None
        blocks.append(
            TextBlock(
                text=anchored_text[m.end():end].strip(),
                page_start=page_start,
                page_end=page_end,
                chunk_id=m.group(3),
                location_confidence=Confidence.HIGH,
            )
        )
    return blocks
