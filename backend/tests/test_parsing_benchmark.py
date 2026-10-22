"""benchmark 解析产物的读取与证据回查。

这一组测试的意义在于把跨模块接口固化下来：benchmark 改了字段形状，这里先红，
而不是等联调或线上才发现证据全部无法定位。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.parsing.anchors import build_anchored_text, collect_anchors, strip_anchors
from app.parsing.base import parse_label_tokens
from app.parsing.benchmark import (
    BenchmarkParseError,
    BenchmarkParser,
    BenchmarkSourceMismatch,
    load_parsed_document,
)
from app.schemas.enums import Confidence, ParseQuality

# data/ 与 backend/ 是兄弟目录；缺少 data/ 时跳过真实数据那条用例。
DATA_REPO = Path(__file__).resolve().parents[2] / "data"
REAL_PARSED = DATA_REPO / "data" / "parsed" / "paper_001" / "v3"
REAL_SOURCE_RECORD = DATA_REPO / "data" / "source_snapshots" / "paper_001" / "source_record.json"


@pytest.fixture
def parsed_dir(tmp_path: Path) -> Path:
    """按 paper_structuring_spec.md v0.1 造一份最小产物。"""
    root = tmp_path / "v1"
    root.mkdir()
    (root / "metadata.json").write_text(
        json.dumps(
            {
                "paper_id": "paper_042",
                "page_count": 9,
                "parse_quality": "good",
                "parse_warnings": ["heading_detection_requires_review"],
                "title": "A Test Paper",
                "language": "en",
            }
        ),
        encoding="utf-8",
    )
    (root / "sections.json").write_text(
        json.dumps(
            [
                {
                    "section_id": "paper_042_v1_sec_1",
                    "title": "Introduction",
                    "level": 1,
                    "page_start": 1,
                    "page_end": 2,
                }
            ]
        ),
        encoding="utf-8",
    )
    (root / "chunks.jsonl").write_text(
        "\n".join(
            json.dumps(c)
            for c in [
                {
                    "chunk_id": "paper_042_v1_chunk_000",
                    "section_id": "paper_042_v1_sec_1",
                    "page_start": 1,
                    "page_end": 3,
                    "content_type": "body_text",
                    "text_content": "We introduce a method and release code.",
                    "location_confidence": "high",
                },
                {
                    "chunk_id": "paper_042_v1_chunk_001",
                    "section_id": "paper_042_v1_sec_1",
                    "page_start": 4,
                    "page_end": 4,
                    "content_type": "body_text",
                    "text_content": "Results are reported on two datasets.",
                    "location_confidence": "low",
                },
            ]
        ),
        encoding="utf-8",
    )
    (root / "elements.json").write_text(
        json.dumps(
            [
                {
                    "element_id": "paper_042_v1_table_1",
                    "element_type": "table",
                    "label": "Table 1",
                    "caption": "Main results",
                    "page_number": 5,
                    "parse_quality": "partial",
                }
            ]
        ),
        encoding="utf-8",
    )
    return root


def test_loads_chunks_sections_and_elements(parsed_dir):
    doc = load_parsed_document(parsed_dir)
    assert doc.paper.paper_id == "paper_042"
    assert doc.parse_quality is ParseQuality.GOOD
    assert [b.chunk_id for b in doc.blocks] == [
        "paper_042_v1_chunk_000",
        "paper_042_v1_chunk_001",
    ]
    assert doc.blocks[0].section_title == "Introduction"
    assert doc.blocks[1].location_confidence is Confidence.LOW
    assert len(doc.elements) == 1


def test_cross_page_chunk_contributes_every_page(parsed_dir):
    """chunk_000 覆盖 1–3 页。只取 page_start 会把第 2、3 页误判成模型编造的。"""
    doc = load_parsed_document(parsed_dir)
    assert doc.known_pages == {1, 2, 3, 4, 5}


def test_index_exposes_what_validation_needs(parsed_dir):
    index = load_parsed_document(parsed_dir).build_index()
    assert index.chunk_ids == {"paper_042_v1_chunk_000", "paper_042_v1_chunk_001"}
    assert index.chunk_confidence["paper_042_v1_chunk_001"] is Confidence.LOW
    assert index.element_pages == {("table", 1): 5}
    assert index.has_elements


def test_anchor_roundtrip_survives_prefixed_chunk_ids(parsed_dir):
    """v0.1 的 [p7|sec_04] 正则匹配不上 paper_001_v3_chunk_000 这种真实 id。"""
    doc = load_parsed_document(parsed_dir)
    anchored = build_anchored_text(doc)
    assert "[p1-3|paper_042_v1_chunk_000]" in anchored
    assert collect_anchors(anchored) == {
        (1, "paper_042_v1_chunk_000"),
        (4, "paper_042_v1_chunk_001"),
    }
    assert "[p" not in strip_anchors(anchored)


def test_missing_files_fail_loudly(tmp_path):
    (tmp_path / "metadata.json").write_text("{}", encoding="utf-8")
    with pytest.raises(BenchmarkParseError):
        load_parsed_document(tmp_path)


def test_chunk_without_page_start_fails_loudly(parsed_dir):
    (parsed_dir / "chunks.jsonl").write_text(
        json.dumps({"chunk_id": "c0", "text_content": "x"}), encoding="utf-8"
    )
    with pytest.raises(BenchmarkParseError, match="page_start"):
        load_parsed_document(parsed_dir)


@pytest.mark.parametrize(
    "label,expected",
    [
        ("Table 1", {("table", 1)}),
        ("Tables 4–6", {("table", 4), ("table", 5), ("table", 6)}),
        ("Figure 2", {("figure", 2)}),
        ("Eq. 3", {("equation", 3)}),
        ("Appendix A", set()),
        (None, set()),
    ],
)
def test_label_tokens(label, expected):
    assert parse_label_tokens(label) == expected


@pytest.mark.skipif(not REAL_PARSED.is_dir(), reason="缺少兄弟目录 data/")
def test_real_paper_001_loads():
    """对着 benchmark 真实产出的 paper_001 v3 跑一遍，形状对不上就在这里红。"""
    doc = load_parsed_document(REAL_PARSED)
    assert doc.paper.paper_id == "paper_001"
    assert doc.paper.page_count == 18
    assert len(doc.blocks) >= doc.paper.page_count
    assert len({block.chunk_id for block in doc.blocks}) == len(doc.blocks)
    assert len(doc.elements) == 16

    index = doc.build_index()
    assert all(chunk_id.startswith("paper_001_v3_chunk_") for chunk_id in index.chunk_ids)
    # ai_trial 的证据引用了 Table 1 / Table 2，必须都能查到
    assert index.element_pages[("table", 1)] == 5
    assert index.element_pages[("table", 2)] == 6
    assert index.pages == set(range(1, 19))


@pytest.mark.skipif(not REAL_PARSED.is_dir(), reason="缺少兄弟目录 data/")
def test_benchmark_parser_loads_only_exact_paper_001():
    parser = BenchmarkParser(
        REAL_PARSED,
        expected_arxiv_id="2307.08122v3",
        source_record=REAL_SOURCE_RECORD,
    )

    doc = parser.parse_arxiv("2307.08122v3")
    assert doc.paper.paper_id == "paper_001"
    assert doc.paper.title == "Tangent Transformers for Composition, Privacy and Removal"
    assert doc.paper.authors == ["Tian Yu Liu", "Aditya Golatkar", "Stefano Soatto"]
    assert doc.paper.arxiv_id == "2307.08122v3"
    assert doc.paper.page_count == 18
    assert len(doc.blocks) >= doc.paper.page_count

    with pytest.raises(BenchmarkSourceMismatch, match="仅支持 2307.08122v3"):
        parser.parse_arxiv("2310.01798")
    with pytest.raises(BenchmarkSourceMismatch, match="不支持任意 PDF"):
        parser.parse_pdf("some-other-paper.pdf")
