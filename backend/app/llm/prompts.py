"""Prompt construction for the analysis pass.

The model is intentionally asked for only the judgment-heavy parts of the
report. Deterministic values such as overall score, rating, and provisional
status are recalculated by app.validation after the LLM response is parsed.
"""
from __future__ import annotations

import json
from textwrap import dedent

from app.config import get_settings
from app.parsing.anchors import build_element_catalog
from app.parsing.base import ParsedDocument
from app.rubrics.loader import LoadedRubric, load_rubric
from app.schemas.report import Report

PROMPT_VERSION = "analysis-v0.4"

SYSTEM_PROMPT = dedent(
    """
    You are a research due-diligence assistant for English machine-learning papers.
    Produce one valid JSON object only. The JSON must match the Unified Field
    Contract v0.4 Report schema used by this backend.

    Product boundary:
    - This is an ICLR-inspired due-diligence scorecard, not an official ICLR review.
    - Do not predict accept/reject decisions or acceptance probability.
    - Do not claim to verify code execution, mathematical proofs, plagiarism,
      retraction status, external SOTA leaderboards, PubPeer, or community signals.
    - Assess SOTA or "best result" claims only as ordinary claim_checks based on
      evidence inside the provided paper text.

    Evidence rules enforced by the backend:
    - Every important judgment must cite evidence_links.
    - For body text evidence, set locator_type to "text". Copy the chunk id from
      an anchor like [p7|paper_chunk_012] into locator_label, and set page_number
      from the same anchor.
    - For tables, figures, equations, or theorems, use only labels listed in the
      ELEMENTS section and set locator_type accordingly.
    - Do not invent page numbers, section ids, chunk ids, quotes, table labels, or
      facts. If evidence is missing or parsing is weak, use confidence "low",
      applicability "uncertain", or checklist status "unable_to_verify".
    - Use evidence_mode "quote" only when the excerpt is copied exactly from the
      provided paper text. Otherwise use "paraphrase".

    JSON requirements:
    - Return exactly the Report object, with no markdown or wrapper key.
    - Include dimensions in this exact order:
      research_value, soundness_evidence, originality_positioning,
      clarity_usability, reproducibility_transparency.
    - Include exactly these reproducibility checks:
      code_availability, data_availability, random_seed, hyperparameters,
      hardware_environment, preprocessing_details.
    - Use snake_case enum values from the contract.
    - For applicable dimensions, score must be an integer 1-10, rating must be
      strong/good/adequate/weak, weight must match the supplied versioned dimension
      contract, and uncertainty_reason must be null.
    - For uncertain dimensions, score must be null, rating must be "uncertain",
      and uncertainty_reason must explain the missing evidence.
    - For not_applicable dimensions, score must be null, rating must be
      "not_applicable", uncertainty_reason must be null, and summary must explain why.
    - Fill overall_assessment fields, but the backend will recompute
      overall_score, rating, is_provisional, and calculation.
    - Keep the response compact: use at most 3 claimed contributions, 3 claim
      checks, 3 strengths, 3 concerns, and 2 unresolved questions. Keep each
      evidence excerpt concise while preserving the exact wording for quotes.
    """
).strip()

_NESTED_OBJECT_GUIDE = dedent(
    """
    Required nested object templates (field names are exact; aliases such as
    details, statement for Finding, or reason for UnresolvedQuestion are invalid):

    DimensionAssessment:
    {
      "dimension_id": "one of the five fixed ids",
      "display_name": "human-readable dimension name",
      "applicability": "applicable|uncertain|not_applicable",
      "score": 1,
      "rating": "strong|good|adequate|weak|uncertain|not_applicable",
      "weight": 0.2,
      "summary": "...",
      "confidence": "high|medium|low",
      "uncertainty_reason": null,
      "evidence_links": [{"evidence_id": "ev_01", "stance": "supports|contradicts|context", "reason": "..."}]
    }

    ReproducibilityCheck:
    {
      "check_id": "one of the six fixed ids",
      "display_name": "human-readable check name",
      "status": "available|not_available|unable_to_verify|not_applicable",
      "detail": "...",
      "evidence_links": []
    }

    Finding (used by both strengths and concerns):
    {
      "finding_id": "strength_01 or concern_01",
      "title": "...",
      "detail": "...",
      "importance": "major|minor",
      "confidence": "high|medium|low",
      "evidence_links": []
    }

    UnresolvedQuestion:
    {
      "question_id": "question_01",
      "question": "...",
      "why_it_matters": "...",
      "evidence_links": []
    }
    """
).strip()


def _report_schema_guide() -> str:
    """Render the actual Pydantic contract so prompt and validator cannot drift."""
    schema = json.dumps(Report.model_json_schema(), ensure_ascii=False, separators=(",", ":"))
    return (
        "Return exactly one JSON object that validates against this JSON Schema. "
        "Every required field must be present and additional properties are forbidden.\n"
        f"{schema}\n\n{_NESTED_OBJECT_GUIDE}"
    )


REPORT_SCHEMA_GUIDE = _report_schema_guide()

CHECKLIST_GUIDE = dedent(
    """
    Fixed reproducibility_checklist ids and display names:
    - code_availability: Code availability
    - data_availability: Data availability
    - random_seed: Random seed
    - hyperparameters: Hyperparameters
    - hardware_environment: Hardware and environment
    - preprocessing_details: Preprocessing details

    Checklist status values:
    - available: explicitly provided in the paper.
    - not_available: searched relevant paper text and the item is not reported.
    - unable_to_verify: parsing or evidence is insufficient to decide.
    - not_applicable: the item does not apply to the paper type or claim.
    """
).strip()


def get_active_rubric() -> LoadedRubric:
    settings = get_settings()
    return load_rubric(settings.rubric_dir, settings.rubric_id, settings.rubric_version)


def build_analysis_prompt(
    doc: ParsedDocument,
    anchored_text: str,
    rubric: LoadedRubric | None = None,
) -> str:
    """Build the user prompt for the report-generation call."""
    rubric = rubric or get_active_rubric()
    catalog = build_element_catalog(doc)
    elements_section = f"ELEMENTS (the only non-text labels you may cite):\n{catalog}\n\n" if catalog else ""
    sections = "\n".join(
        f"- {s.section_id}: {s.title} (level {s.level}, pages {s.page_start}-{s.page_end or s.page_start})"
        for s in doc.paper.sections
    )
    parse_warnings = "\n".join(f"- {w}" for w in doc.warnings or doc.paper.parse_warnings) or "- none"
    rubric_contract = "\n".join(
        f"- {dimension.dimension_id}: weight={dimension.weight}"
        for dimension in rubric.definition.dimensions
    )

    return dedent(
        f"""
        Paper metadata:
        - paper_id: {doc.paper.paper_id}
        - title: {doc.paper.title}
        - authors: {", ".join(doc.paper.authors) if doc.paper.authors else "unknown or not provided"}
        - abstract: {doc.paper.abstract}
        - language: {doc.paper.language}
        - page_count: {doc.paper.page_count}
        - arxiv_id: {doc.paper.arxiv_id}
        - doi: {doc.paper.doi}
        - parse_quality: {doc.parse_quality.value}
        - parse_warnings:
        {parse_warnings}

        Sections:
        {sections or "- no reliable section structure was parsed"}

        {elements_section}
        VERSIONED RUBRIC ({rubric.rubric_id} / {rubric.rubric_version}, sha256={rubric.content_sha256}):
        Dimension contract:
        {rubric_contract}

        {rubric.scoring_guide}

        {CHECKLIST_GUIDE}

        {REPORT_SCHEMA_GUIDE}

        Full paper text with location anchors:
        {anchored_text}

        Produce the JSON report now. Use only the supplied paper text and metadata.
        """
    ).strip()
