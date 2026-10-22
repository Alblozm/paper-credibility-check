"""Discovery intake boundaries; all observations are local fixture responses."""

import base64
import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.credibility import code_checks
from app.credibility.code_checks import (
    check_code,
    complete_confirmed_repositories,
    intake_discovered_repositories,
)
from app.credibility.code_lookup import Fetch
from app.credibility.context import CheckContext
from app.credibility.models import CredibilityCodeDiscovery, CredibilityEvidence, CredibilityExternalSource
from app.credibility.snapshots import capture_snapshot
from app.parsing.base import ParsedDocument, TextBlock
from app.schemas.enums import ParseQuality
from app.schemas.paper import Paper

TITLE = "The Expressive Power of Transformers with Chain of Thought"
SHA = "a" * 40
STAMP = "2026-10-19T00:00:00Z"


@pytest.fixture(autouse=True)
def isolated_archives(tmp_path, monkeypatch):
    monkeypatch.setenv("CREDIBILITY_ARTIFACT_DIR", str(tmp_path / "artifacts"))


def context():
    return CheckContext(
        source={"type": "arxiv", "arxiv_id": "2310.07923v5"},
        repo_root=Path("."),
        generated_at=STAMP,
        ref=None,
        unresolved_reason=None,
        doc=ParsedDocument(
            paper=Paper(
                paper_id="cot",
                title=TITLE,
                authors=["William Merrill", "Ashish Sabharwal"],
                arxiv_id="2310.07923v5",
                parse_quality=ParseQuality.GOOD,
            ),
            blocks=[TextBlock(text="We conduct experiments.", chunk_id="p1", page_start=1)],
        ),
    )


def base_result():
    result = check_code(context())
    url = "https://search.example/results"
    data = {"query": TITLE, "results": [{"url": "https://github.com/author/method"}]}
    ref = capture_snapshot(
        kind="metadata",
        raw=json.dumps(data).encode(),
        normalized_json=data,
        requested_url=url,
        final_url=url,
        retrieved_at=STAMP,
        fetch_status="ok",
        http_status=200,
    )
    result.sources.append(
        CredibilityExternalSource(
            source_id="discovery_src_1",
            kind="metadata",
            url=url,
            fetch_status="ok",
            http_status=200,
            retrieved_at=STAMP,
            observed_at=STAMP,
            is_cached=False,
            snapshot_ref=ref,
            association_reason="Search lead only; not proof of official implementation.",
        )
    )
    result.evidence.append(
        CredibilityEvidence(
            evidence_id="discovery_ev_1",
            origin="external",
            mode="quote",
            excerpt=TITLE,
            paper_locator=None,
            source_id="discovery_src_1",
            location_confidence="high",
        )
    )
    result.discovery = CredibilityCodeDiscovery(status="completed", reason="Bounded search fixture completed.")
    return result


def candidate(url="https://github.com/author/method", cid="candidate_1"):
    return {"candidate_id": cid, "url": url, "source_ids": ["discovery_src_1"], "evidence_ids": ["discovery_ev_1"]}


def network(monkeypatch, *, readme=None, canonical=None, fail_metadata=False):
    calls = []
    readme = readme if readme is not None else f"# {TITLE}\nOfficial implementation for arxiv:2310.07923."

    def get(url):
        calls.append(url)
        if "/commits/" in url:
            data = {"sha": SHA}
        elif "/readme" in url:
            data = {"encoding": "base64", "content": base64.b64encode(readme.encode()).decode()}
        elif "/git/trees/" in url:
            data = {"tree": [{"path": "method.py", "type": "blob"}]}
        elif "/contents/method.py" in url:
            data = {"encoding": "base64", "content": base64.b64encode(b"def research():\n    return 42\n").decode()}
        elif "/issues?" in url:
            data = []
        else:
            assert url.count("/") == 5, url
            if fail_metadata:
                return Fetch(url, "rate_limited", 429, STAMP)
            data = {"full_name": canonical or url.split("/repos/", 1)[1], "default_branch": "main"}
        return Fetch(url, "ok", 200, STAMP, data=data)

    monkeypatch.setattr(code_checks.LOOKUP, "get", get)
    return calls


@pytest.mark.parametrize(
    "readme",
    [
        f"# {TITLE}\nOfficial implementation for arxiv:2310.07923.",
        f"# Benchmark source code\nOur code is available here.\n## Related reading\n{TITLE}\nhttps://arxiv.org/abs/2310.07923",
    ],
)
def test_discovery_readme_and_reading_list_never_automatically_confirm(monkeypatch, readme):
    result = base_result()
    calls = network(monkeypatch, readme=readme)
    output = intake_discovered_repositories(context(), result, [candidate()])
    repo = output.checks.repositories[0]
    assert repo.association == "uncertain" and repo.association_evidence_ids == []
    assert repo.inspected_revision == SHA and repo.implementation_evidence_ids == []
    assert output.checks.code_availability == "unable_to_verify"
    assert len(calls) == 3 and not any("/issues" in url or "/git/trees/" in url for url in calls)
    assert output.followup_state[repo.repository_id]["discovery_intake_only"] is True


def test_intake_preserves_old_ids_review_discovery_and_input(monkeypatch):
    result = base_result()
    result.prompt_version = "prior-review"
    result.external_review = {"status": "completed", "audit_ref": None}
    result.findings.append(result.findings[0].model_copy(update={"finding_id": "existing_context"}))
    before = result.model_dump()
    network(monkeypatch)
    output = intake_discovered_repositories(context(), result, [candidate()])
    assert result.model_dump() == before
    assert output.sources[0] == result.sources[0] and output.evidence[0] == result.evidence[0]
    assert output.discovery == result.discovery and output.external_review == result.external_review
    assert output.prompt_version == result.prompt_version
    assert "existing_context" in output.dimension.finding_ids
    leads = output.followup_state[output.checks.repositories[0].repository_id]["discovery_candidates"]
    assert leads == [candidate()]


def test_duplicate_identity_case_git_suffix_reuses_repository_and_adds_only_leads(monkeypatch):
    calls = network(monkeypatch)
    output = intake_discovered_repositories(
        context(), base_result(), [candidate(), candidate("https://github.com/AUTHOR/METHOD.git/", "candidate_2")]
    )
    assert len(output.checks.repositories) == 1 and len(calls) == 3
    again = intake_discovered_repositories(context(), output, [candidate()])
    assert len(calls) == 3 and again.model_dump() == output.model_dump()


def test_metadata_migration_alias_reuses_same_observed_repository(monkeypatch):
    calls = network(monkeypatch, canonical="newauthor/method")
    output = intake_discovered_repositories(
        context(), base_result(), [candidate(), candidate("https://github.com/newauthor/method", "candidate_2")]
    )
    assert len(output.checks.repositories) == 1 and len(calls) == 3


def test_paper_linked_repository_keeps_original_ownership_and_origin_on_duplicate_intake(monkeypatch):
    ctx = context()
    ctx.doc.blocks[0].text = "Our code is available at https://github.com/author/method."
    calls = network(monkeypatch)
    result = check_code(ctx)
    leads = base_result()
    result.sources.extend(leads.sources)
    result.evidence.extend(leads.evidence)
    result.discovery = leads.discovery
    before = list(calls)
    output = intake_discovered_repositories(ctx, result, [candidate()])
    assert calls == before and len(output.checks.repositories) == 1
    assert output.checks.code_availability == "available"
    assert output.checks.repositories[0].association == "confirmed"
    assert not output.followup_state["code_repo_1"].get("discovery_intake_only")


def test_four_tuple_candidate_uses_the_same_source_bound_intake(monkeypatch):
    calls = network(monkeypatch)
    row = candidate()
    output = intake_discovered_repositories(context(), base_result(), [tuple(row.values())])
    assert len(calls) == 3
    assert output.followup_state["code_repo_1"]["discovery_candidates"] == [row]


def test_pinned_subtrees_are_kept_separate_and_never_use_default_head(monkeypatch):
    calls = network(monkeypatch)
    output = intake_discovered_repositories(
        context(),
        base_result(),
        [
            candidate(f"https://github.com/author/method/tree/{SHA}/paper_a"),
            candidate(f"https://github.com/author/method/tree/{SHA}/paper_b", "candidate_2"),
        ],
    )
    assert len(output.checks.repositories) == 2
    assert {state["inspected_subdir"] for state in output.followup_state.values()} == {"paper_a", "paper_b"}
    assert not any("/commits/main" in url for url in calls)
    assert any(f"/readme/paper_a?ref={SHA}" in url for url in calls)
    assert any(f"/readme/paper_b?ref={SHA}" in url for url in calls)


def test_intake_total_repository_limit_includes_existing_candidates(monkeypatch):
    calls = network(monkeypatch)
    first = intake_discovered_repositories(context(), base_result(), [candidate()])
    output = intake_discovered_repositories(
        context(), first, [candidate(f"https://github.com/author/method{i}", f"candidate_{i}") for i in range(2, 7)]
    )
    assert len(output.checks.repositories) == 4 and len(calls) == 12
    assert any("candidate limit" in reason for reason in output.checks.scope_limitations)


@pytest.mark.parametrize("requests", [39, 40])
def test_intake_shares_request_budget_and_keeps_failure_partial(monkeypatch, requests):
    result = base_result()
    result.external_request_count = requests
    calls = network(monkeypatch)
    output = intake_discovered_repositories(context(), result, [candidate()])
    assert len(calls) == 40 - requests and output.external_request_count == 40
    assert output.dimension.check_status == "partial"
    assert output.checks.code_availability == "unable_to_verify"
    assert not output.checks.repositories[0].implementation_evidence_ids


def test_failed_discovered_metadata_is_audited_not_repository_absence(monkeypatch):
    calls = network(monkeypatch, fail_metadata=True)
    output = intake_discovered_repositories(context(), base_result(), [candidate()])
    assert len(calls) == 1 and output.dimension.check_status == "partial"
    assert output.checks.repositories[0].availability == "unknown"
    assert output.sources[-1].fetch_status == "rate_limited"
    assert output.checks.code_availability == "unable_to_verify"


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/author/method",
        "https://u:p@github.com/author/method",
        "https://github.com/author/method/issues/1",
        "https://github.com/search?q=paper",
        "https://github.com/author/method?ref=main",
        "https://github.com/author/method/tree",
        "https://github.com/author/method/tree/main/../other",
        "https://github.com/author/method/tree/main//other",
        "https://github.com/author/method/tree/main/%2fother",
        "https://127.0.0.1/author/method",
    ],
)
def test_invalid_discovered_scope_never_fetches(monkeypatch, url):
    calls = network(monkeypatch)
    output = intake_discovered_repositories(context(), base_result(), [candidate(url)])
    assert calls == [] and not output.checks.repositories and output.dimension.check_status == "partial"


@pytest.mark.parametrize("field,values", [("source_ids", ["unknown_source"]), ("evidence_ids", ["unknown_evidence"])])
def test_unregistered_binding_never_grants_intake(monkeypatch, field, values):
    calls = network(monkeypatch)
    row = candidate()
    row[field] = values
    output = intake_discovered_repositories(context(), base_result(), [row])
    assert calls == [] and output.dimension.check_status == "partial"


def test_missing_archive_cannot_be_replaced_by_a_search_excerpt(monkeypatch):
    calls = network(monkeypatch)
    result = base_result()
    result.sources[0].snapshot_ref = "snapshot:v1:" + "f" * 64
    output = intake_discovered_repositories(context(), result, [candidate()])
    assert calls == [] and not output.checks.repositories and output.dimension.check_status == "partial"


def test_missing_input_and_empty_intake_do_not_rebuild_producer_completed(monkeypatch):
    calls = network(monkeypatch)
    ctx = context()
    result = check_code(replace(ctx, doc=None))
    assert intake_discovered_repositories(replace(ctx, doc=None), result, [candidate()]) == result
    assert intake_discovered_repositories(ctx, result, []) == result
    assert calls == []


def test_validated_confirmation_reuses_intake_sha_and_preserves_discovery(monkeypatch):
    calls = network(monkeypatch)
    result = intake_discovered_repositories(context(), base_result(), [candidate()])
    result.checks.repositories[0].association = "confirmed"  # Already validated model output, not discovery output.
    before = list(calls)
    output = complete_confirmed_repositories(context(), result)
    assert output.discovery == result.discovery
    assert output.checks.code_availability == "available"
    assert output.checks.repositories[0].inspected_revision == SHA
    assert output.followup_state["code_repo_1"]["discovery_candidates"] == [candidate()]
    assert not any("/readme" in url or "/commits/" in url for url in calls[len(before) :])
    assert complete_confirmed_repositories(context(), output).model_dump() == output.model_dump()
