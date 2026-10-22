"""Independent discovery review regressions: no HTTP and no real model calls."""

import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.credibility import execution
from app.credibility.code_checks import check_code
from app.credibility.code_discovery import DiscoverySession, implementation_relation
from app.credibility.code_discovery import _Judgments as _DiscoveryJudgments
from app.credibility.code_model import ExternalEvidenceInvalid, _Judgments, _validate_and_apply
from app.credibility.context import CheckContext
from app.credibility.external_tools import ExternalToolSession
from app.credibility.models import CredibilityCodeDiscovery, CredibilityExternalSource, CredibilityRepository
from app.credibility.snapshots import capture_snapshot
from app.parsing.base import ParsedDocument, TextBlock
from app.schemas.enums import ParseQuality
from app.schemas.paper import Paper

TITLE = "The Expressive Power of Transformers with Chain of Thought"
STAMP = "2026-10-19T00:00:00Z"


def context():
    return CheckContext(
        source={"type": "arxiv", "arxiv_id": "2310.07923v5"},
        repo_root=Path("."),
        generated_at=STAMP,
        ref=None,
        unresolved_reason=None,
        doc=ParsedDocument(
            paper=Paper(paper_id="cot", title=TITLE, arxiv_id="2310.07923v5", parse_quality=ParseQuality.GOOD),
            blocks=[TextBlock(text="We conduct experiments.", chunk_id="p1", page_start=1)],
        ),
        model_id="test-model",
    )


@pytest.mark.parametrize(
    "text",
    [
        f"This is not an official implementation of {TITLE} (2310.07923).",
        f"We do not implement {TITLE} (2310.07923).",
        f"Official implementation for A Different Paper.\n\nOther resources:\n{TITLE} (2310.07923).",
        f"Official implementation for A Different Paper, compared against {TITLE} (2310.07923).",
    ],
)
def test_negated_or_other_paper_implementation_is_not_an_ownership_relation(text):
    assert not implementation_relation(context(), [text])


def test_positive_explicit_same_paper_relation_remains_supported():
    assert implementation_relation(context(), [f"Official implementation for {TITLE} (2310.07923)."])


@pytest.mark.parametrize("quote_full_source", [True, False])
def test_wrong_candidate_label_retains_actual_third_party_implementation_without_official_intake(
    tmp_path, monkeypatch, quote_full_source
):
    monkeypatch.setenv("CREDIBILITY_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    ctx = context()
    result = check_code(ctx)
    session = DiscoverySession(ctx, result, SimpleNamespace(request_count=0))
    row = session.register("https://github.com/reader/reproduction")
    body = f"Implementation for {TITLE} (2310.07923)."
    text = "# Unofficial third-party reproduction\n\n" + body
    source_id = "third_party_page"
    snapshot = capture_snapshot(
        kind="project_page",
        normalized_text=text,
        requested_url=row.url,
        final_url=row.url,
        fetch_status="ok",
        http_status=200,
        retrieved_at=STAMP,
    )
    result.sources.append(
        CredibilityExternalSource(
            source_id=source_id,
            kind="project_page",
            url=row.url,
            fetch_status="ok",
            http_status=200,
            retrieved_at=STAMP,
            observed_at=STAMP,
            is_cached=False,
            snapshot_ref=snapshot,
            association_reason="Real third-party implementation fixture.",
        )
    )
    row.source_ids = [source_id]
    session.documents[source_id] = text
    session.candidate_origins[row.candidate_id] = {source_id}
    judgments = _DiscoveryJudgments.model_validate(
        {
            "reason": "Found a real implementation.",
            "candidates": [
                {
                    "candidate_id": row.candidate_id,
                    "resource_kind": "implementation_candidate",
                    "reason": "Model failed to preserve the explicit third-party label.",
                    "citations": [{"source_id": source_id, "excerpt": text if quote_full_source else body}],
                }
            ],
        }
    )
    assert session.apply(judgments) == []
    assert row.resource_kind == "third_party_reproduction" and row.status == "candidate"
    assert row.evidence_ids  # Keep useful public implementation evidence; no official-code points.


@pytest.mark.parametrize(
    "declaration,quote_full_source",
    [
        ("an unofficial implementation", True),
        ("a third-party implementation", True),
        ("an implementation", False),
    ],
)
def test_explicit_third_party_readme_cannot_be_confirmed_as_official_code(
    tmp_path, monkeypatch, declaration, quote_full_source
):
    monkeypatch.setenv("CREDIBILITY_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    ctx = context()
    result = check_code(ctx)
    api, sha = "https://api.github.com/repos/author/method", "a" * 40
    text = f"This repository provides {declaration} for {TITLE} (2310.07923)."
    quoted = text
    if not quote_full_source:
        text = "# Unofficial third-party reproduction\n\n" + text
    for sid, url, data in [
        ("repo_source", api, {"full_name": "author/method", "private": False}),
        (
            "readme_source",
            api + "/readme?ref=" + sha,
            {"encoding": "base64", "content": base64.b64encode(text.encode()).decode()},
        ),
    ]:
        ref = capture_snapshot(
            kind="repository",
            normalized_json=data,
            requested_url=url,
            final_url=url,
            fetch_status="ok",
            http_status=200,
            retrieved_at=STAMP,
        )
        result.sources.append(
            CredibilityExternalSource(
                source_id=sid,
                kind="repository",
                url=url,
                fetch_status="ok",
                http_status=200,
                retrieved_at=STAMP,
                observed_at=STAMP,
                is_cached=False,
                snapshot_ref=ref,
                association_reason="Actual pinned repository fixture.",
            )
        )
    repo = CredibilityRepository(
        repository_id="repo_1",
        source_id="repo_source",
        association="uncertain",
        association_reason="Search candidate.",
        association_evidence_ids=[],
        availability="accessible",
        implementation_evidence_ids=[],
        inspected_revision=sha,
    )
    result.checks.repositories = [repo]
    result.followup_state[repo.repository_id] = {"discovery_intake_only": True}
    session = ExternalToolSession(ctx, result, max_calls=0)
    session.existing_document(result.sources[-1])
    judgments = _Judgments.model_validate(
        {
            "repositories": [
                {
                    "repository_id": repo.repository_id,
                    "association": "confirmed",
                    "reason": "Model wrongly promotes it.",
                    "citations": [{"source_id": "readme_source", "excerpt": quoted}],
                }
            ],
            "discussions": [],
        }
    )
    with pytest.raises(ExternalEvidenceInvalid):
        _validate_and_apply(ctx, result, session, judgments, [repo], [])


@pytest.mark.parametrize("old_status", ["partial", "failed"])
def test_reused_discovery_failure_cannot_be_upgraded_by_no_target_semantics(tmp_path, monkeypatch, old_status):
    monkeypatch.setenv("CREDIBILITY_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    ctx = context()
    result = check_code(ctx)
    result.dimension.check_status = "partial"
    source_url = "https://evidence.example/search"
    source_ref = capture_snapshot(
        kind="metadata",
        normalized_json={"hits": []},
        requested_url=source_url,
        final_url=source_url,
        fetch_status="ok",
        http_status=200,
        retrieved_at=STAMP,
    )
    result.sources.append(
        CredibilityExternalSource(
            source_id="search_source",
            kind="metadata",
            url=source_url,
            fetch_status="ok",
            http_status=200,
            retrieved_at=STAMP,
            observed_at=STAMP,
            is_cached=False,
            snapshot_ref=source_ref,
            association_reason="Successful HTTP before incomplete discovery model output.",
        )
    )
    old_audit = capture_snapshot(kind="code_discovery_audit", normalized_json={"status": old_status})
    result.discovery = CredibilityCodeDiscovery(
        status=old_status, reason="No valid final discovery output.", audit_ref=old_audit
    )
    ctx.previous_check = result.model_dump(mode="json")
    settings = SimpleNamespace(
        credibility_code_model_enabled=True,
        credibility_code_discovery_enabled=True,
        credibility_code_max_rounds=3,
        credibility_code_max_tool_calls=6,
        credibility_code_tool_mode="native_tools",
        credibility_code_max_characters=60000,
        credibility_code_max_output_tokens=8000,
        credibility_code_discovery_max_rounds=4,
        credibility_code_discovery_max_tool_calls=8,
    )
    monkeypatch.setattr(execution, "get_settings", lambda: settings)
    monkeypatch.setattr("app.pipeline.orchestrator.build_llm_client", lambda _: object())
    monkeypatch.setattr("app.credibility.code_checks.LOOKUP.get", lambda _: pytest.fail("No HTTP permitted"))
    output = execution.check_code_with_model(ctx)
    assert output.discovery.status == old_status
    assert output.dimension.check_status == "partial"
