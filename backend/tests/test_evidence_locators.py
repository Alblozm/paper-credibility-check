"""证据定位核对：表图编号、chunk 引用、解析置信度上限、解析质量闸门。

对应 paper_structuring_spec.md v0.1 —— 解析产物是结构化记录，
这些都能确定性核对，不必再调模型。
"""
from __future__ import annotations

import pytest

from app.parsing.base import PaperElement, PaperLink, ParsedDocument, TextBlock
from app.schemas.enums import ChecklistStatus, Confidence, ParseQuality
from app.schemas.paper import Paper
from app.validation.report_validator import validate_report_payload


def _doc(parse_quality: ParseQuality = ParseQuality.GOOD, **kw) -> ParsedDocument:
    paper = Paper(paper_id="p1", parse_quality=parse_quality)
    blocks = [
        TextBlock(
            text="The paper motivates the problem and reports results.",
            page_start=1,
            page_end=3,
            chunk_id="p1_v1_chunk_000",
            section_id="sec_01",
            location_confidence=Confidence.HIGH,
        ),
        TextBlock(
            text="Appendix implementation details with a repository link.",
            page_start=10,
            chunk_id="p1_v1_chunk_001",
            section_id="sec_appendix",
            location_confidence=Confidence.LOW,
        ),
    ]
    elements = [
        PaperElement(
            element_id="p1_v1_table_2",
            element_type="table",
            label="Table 2",
            page_number=6,
        )
    ]
    return ParsedDocument(
        paper=paper,
        blocks=blocks,
        elements=elements,
        parse_quality=parse_quality,
        **kw,
    )


@pytest.fixture
def index():
    return _doc().build_index()


def _codes(result) -> set[str]:
    return {i.code for i in result.issues}


def test_known_table_label_passes(example_report, index):
    """示例报告引用 Table 2，解析结果里确实有 Table 2 且在第 6 页。"""
    result = validate_report_payload(example_report, source_index=index)
    assert result.ok
    assert "EVIDENCE_LOCATOR_UNKNOWN" not in _codes(result)
    ev2 = next(e for e in result.report.evidence if e.evidence_id == "ev_02")
    assert ev2.location_confidence is Confidence.HIGH
    assert ev2.page_number == 6


def test_fabricated_table_is_flagged(example_report, index):
    example_report["evidence"][1]["locator_label"] = "Table 9"
    result = validate_report_payload(example_report, source_index=index)
    assert "EVIDENCE_LOCATOR_UNKNOWN" in _codes(result)
    ev2 = next(e for e in result.report.evidence if e.evidence_id == "ev_02")
    assert ev2.location_confidence is Confidence.LOW


def test_label_range_hits_are_accepted(example_report, index):
    """模型爱写「Tables 1–2」这种区间，命中任意一个就算能定位。"""
    example_report["evidence"][1]["locator_label"] = "Tables 1–2"
    result = validate_report_payload(example_report, source_index=index)
    assert "EVIDENCE_LOCATOR_UNKNOWN" not in _codes(result)


def test_wrong_page_for_known_table_is_corrected(example_report, index):
    """编号对得上、页码对不上时以解析结果为准。"""
    example_report["evidence"][1]["page_number"] = 4
    result = validate_report_payload(example_report, source_index=index)
    assert "EVIDENCE_LOCATOR_PAGE_FIXED" in _codes(result)
    ev2 = next(e for e in result.report.evidence if e.evidence_id == "ev_02")
    assert ev2.page_number == 6


def test_unknown_chunk_reference_is_flagged(example_report, index):
    example_report["evidence"][0]["locator_label"] = "p1_v1_chunk_777"
    result = validate_report_payload(example_report, source_index=index)
    assert "EVIDENCE_CHUNK_UNKNOWN" in _codes(result)


def test_free_form_label_is_not_treated_as_fabricated(example_report, index):
    """「Appendix A」认不出编号，属于无法核对，不该判失败。"""
    example_report["evidence"][0]["locator_label"] = "Appendix A"
    result = validate_report_payload(example_report, source_index=index)
    assert "EVIDENCE_CHUNK_UNKNOWN" not in _codes(result)
    assert "EVIDENCE_LOCATOR_UNKNOWN" not in _codes(result)


def test_confidence_capped_by_parser_self_assessment(example_report, index):
    """解析器说这块位置只有 low 置信度，模型没有依据说 high。"""
    example_report["evidence"][0]["locator_label"] = "p1_v1_chunk_001"
    example_report["evidence"][0]["location_confidence"] = "high"
    result = validate_report_payload(example_report, source_index=index)
    assert "EVIDENCE_CONFIDENCE_CAPPED" in _codes(result)
    assert result.report.evidence[0].location_confidence is Confidence.LOW


def test_confidence_is_never_raised(example_report, index):
    """封顶只往下压：证据是 low、解析器是 high 时保持 low。"""
    example_report["evidence"][0]["locator_label"] = "p1_v1_chunk_000"
    example_report["evidence"][0]["location_confidence"] = "low"
    result = validate_report_payload(example_report, source_index=index)
    assert result.report.evidence[0].location_confidence is Confidence.LOW
    assert "EVIDENCE_CONFIDENCE_CAPPED" not in _codes(result)


def test_cross_page_chunk_pages_are_not_rejected(example_report, index):
    """chunk_000 覆盖 1–3 页；第 2 页是合法页码，不该被当成编造的。"""
    example_report["evidence"][0]["page_number"] = 2
    result = validate_report_payload(example_report, source_index=index)
    assert "EVIDENCE_PAGE_UNKNOWN" not in _codes(result)
    assert result.report.evidence[0].page_number == 2


def test_page_outside_parsed_range_still_rejected(example_report, index):
    example_report["evidence"][0]["page_number"] = 99
    result = validate_report_payload(example_report, source_index=index)
    assert "EVIDENCE_PAGE_UNKNOWN" in _codes(result)
    assert result.report.evidence[0].page_number is None


def test_poor_parse_forces_provisional(example_report):
    index = _doc(parse_quality=ParseQuality.POOR).build_index()
    result = validate_report_payload(example_report, source_index=index)
    assert "PARSE_QUALITY_POOR" in _codes(result)
    assert result.report.overall_assessment.is_provisional is True


def test_good_parse_does_not_force_provisional(example_report, index):
    result = validate_report_payload(example_report, source_index=index)
    assert "PARSE_QUALITY_POOR" not in _codes(result)


def test_parser_code_link_prevents_false_downgrade(example_report):
    """解析器已抽到仓库链接，就别因为正则没扫到而把 code_availability 降级。"""
    doc = _doc()
    doc.links = [PaperLink(url="https://github.com/example/repo", link_type="code")]
    checks = {c["check_id"]: c for c in example_report["reproducibility_checklist"]}
    checks["code_availability"]["status"] = "available"

    without = validate_report_payload(example_report, source_index=_doc().build_index())
    assert "CHECKLIST_DOWNGRADED" in _codes(without)

    with_link = validate_report_payload(example_report, source_index=doc.build_index())
    code_check = next(
        c for c in with_link.report.reproducibility_checklist
        if c.check_id == "code_availability"
    )
    assert code_check.status is ChecklistStatus.AVAILABLE


def test_warnings_reach_the_frontend(example_report, index):
    """所有自动修正都要转成契约 §17.1 的 warning，不能静默吞掉。"""
    example_report["evidence"][1]["locator_label"] = "Table 9"
    result = validate_report_payload(example_report, source_index=index)
    codes = {w.code for w in result.to_warnings("linking_evidence")}
    assert "EVIDENCE_LOCATOR_UNVERIFIED" in codes


def test_pdf_split_repo_url_is_still_a_code_signal():
    """真实 paper_001 抽出来的样子：换行让 URL 断成两截。

    严格 URL 正则匹配不到，于是把明明公开了代码的论文降成 unable_to_verify。
    降级只能因为「确实没有」，不能因为解析器把地址拆断了。
    """
    from app.validation.crosscheck import extract_repo_urls, scan_source

    text = (
        "Our code is available at: https://github.com/ "
        "tianyu139/tangent-model-composition 1 I NTRODUCTION"
    )
    assert scan_source(text)["code_availability"] is True
    assert extract_repo_urls(text) == [
        "https://github.com/tianyu139/tangent-model-composition"
    ]


def test_absent_repo_still_downgrades():
    """反向确认：宽松之后也不能变成「什么都算有代码」。"""
    from app.validation.crosscheck import scan_source

    assert scan_source("A purely theoretical paper with proofs only.")["code_availability"] is False
