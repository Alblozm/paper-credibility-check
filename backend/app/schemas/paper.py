"""契约 §8 Paper 字段。"""
from __future__ import annotations

from app.schemas.common import StrictModel
from app.schemas.enums import ParseQuality


class Section(StrictModel):
    """契约 §8.1。"""

    section_id: str
    title: str
    level: int
    page_start: int | None = None
    page_end: int | None = None


class Paper(StrictModel):
    paper_id: str
    title: str | None = None
    authors: list[str] = []          # 匿名或缺失时是 []，不是 null
    abstract: str | None = None
    language: str | None = None
    page_count: int | None = None
    arxiv_id: str | None = None
    doi: str | None = None
    sections: list[Section] = []
    parse_quality: ParseQuality
    parse_warnings: list[str] = []
