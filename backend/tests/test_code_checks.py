"""Task-three boundary, evidence and network tests. All responses are fixtures."""

from __future__ import annotations

import base64
from pathlib import Path

import httpx
import pytest

from app.credibility import code_checks, code_lookup
from app.credibility.code_checks import check_code, classify_feedback
from app.credibility.code_lookup import Fetch, PublicCodeLookup
from app.credibility.context import CheckContext
from app.credibility.locate import assert_report_links
from app.credibility.shell import assemble_report
from app.parsing.base import PaperLink, ParsedDocument, TextBlock
from app.schemas.enums import ParseQuality
from app.schemas.paper import Paper

API = "https://api.github.com/repos/alice/method"
SHA = "a" * 40
TITLE = "A Reliable Method for Learning"


def context(text="Our code is available at https://github.com/alice/method.", *, role="introduction"):
    return CheckContext(
        source={"type": "arxiv", "arxiv_id": "2401.12345"},
        repo_root=Path("."),
        generated_at="2026-10-17T00:00:00Z",
        ref=None,
        unresolved_reason=None,
        doc=ParsedDocument(
            paper=Paper(
                paper_id="test",
                title=TITLE,
                authors=["Alice Smith"],
                arxiv_id="2401.12345",
                parse_quality=ParseQuality.GOOD,
            ),
            blocks=[TextBlock(text=text, chunk_id="chunk1", page_start=2, section_role=role)],
        ),
    )


def response(url, data=None, *, status="ok", text="", next_page=False):
    return Fetch(
        url, status, 200 if status == "ok" else None, "2026-10-17T00:00:00Z", text=text, data=data, has_next=next_page
    )


def network(monkeypatch, *, issues=None, comments=None, readme=None, overrides=None):
    readme = (
        readme
        if readme is not None
        else f"# {TITLE}\nOfficial implementation.\n[Alice Smith](https://github.com/alice)"
    )
    routes = {
        API: {"full_name": "alice/method", "default_branch": "main"},
        API + "/readme?ref=" + SHA: {"encoding": "base64", "content": base64.b64encode(readme.encode()).decode()},
        API + "/contents/train.py?ref=" + SHA: {
            "encoding": "base64",
            "content": base64.b64encode(b"def train():\n    return 42\n").decode(),
        },
        API + "/commits/main": {"sha": SHA},
        API + f"/git/trees/{SHA}?recursive=1": {"tree": [{"path": "train.py", "type": "blob"}]},
        API + "/issues?state=all&sort=updated&per_page=20&page=1": issues or [],
        API + "/issues/1/comments?per_page=100&page=1": comments or [],
        **(overrides or {}),
    }
    called = []

    def get(url):
        called.append(url)
        assert url in routes, f"unexpected request {url}"
        value = routes[url]
        return value if isinstance(value, Fetch) else response(url, value)

    monkeypatch.setattr(code_checks.LOOKUP, "get", get)
    return called


def issue(body, *, count=0, **extra):
    return {
        "number": 1,
        "title": "Reproduction results",
        "body": body,
        "comments": count,
        "html_url": "https://github.com/alice/method/issues/1",
        "state": "closed",
        "user": {"login": "reader"},
        "created_at": "2026-01-01",
        **extra,
    }


def test_confirmed_code_has_real_paper_and_external_locations(monkeypatch):
    network(monkeypatch)
    ctx = context()
    result = check_code(ctx)
    assert result.checks.code_availability == "available"
    assert result.checks.reproduction_signal == "no_feedback_found"
    assert result.checks.scope_complete is False
    repo = result.checks.repositories[0]
    assert repo.association == "confirmed" and repo.inspected_revision == SHA
    assert next(e for e in result.evidence if e.origin == "paper").paper_locator.page_start == 2
    report, _ = assemble_report(ctx, report_id="test_report")
    assert_report_links(report)
    assert report.overall.score is None and report.overall.category is None


def test_truncated_github_link_is_not_repository(monkeypatch):
    monkeypatch.setattr(code_checks.LOOKUP, "get", lambda url: pytest.fail("must not fetch GitHub root"))
    ctx = context("Our code is available at https://github.com/.")
    ctx.doc.links = [PaperLink("https://github.com/", "code", "chunk1")]
    result = check_code(ctx)
    assert result.checks.repositories == []
    assert result.checks.code_availability != "available"


def test_unconfirmed_candidate_never_scans_issues(monkeypatch):
    called = network(monkeypatch, readme="# A different paper\nSource code for a baseline.")
    ctx = context("We compare against previous work.")
    ctx.doc.links = [PaperLink("https://github.com/alice/method", "code")]
    result = check_code(ctx)
    assert result.checks.repositories[0].association == "uncertain"
    assert not any("/issues" in url for url in called)


def test_reference_url_is_not_direct_ownership(monkeypatch):
    called = network(monkeypatch, readme="Citation list only")
    ctx = context("Our code is available at https://github.com/alice/method.", role="references")
    ctx.doc.links = [PaperLink("https://github.com/alice/method", "code", "chunk1")]
    result = check_code(ctx)
    assert result.checks.repositories[0].association == "uncertain"
    assert not any("/issues" in url for url in called)


def test_readme_can_confirm_links_json_candidate(monkeypatch):
    network(monkeypatch)
    ctx = context("We conduct experiments.")
    ctx.doc.links = [PaperLink("https://github.com/alice/method", "code")]
    assert check_code(ctx).checks.repositories[0].association == "confirmed"


def test_success_failure_and_author_reply_remain_traceable(monkeypatch):
    reply = {
        "body": "We have reproduced the results with the updated checkpoint.",
        "html_url": "https://github.com/alice/method/issues/1#issuecomment-9",
        "user": {"login": "alice"},
        "author_association": "OWNER",
    }
    network(monkeypatch, issues=[issue("I cannot reproduce the accuracy.", count=1)], comments=[reply])
    result = check_code(context())
    discussion = result.checks.discussions[0]
    assert discussion.signal == "failure_reported"
    assert discussion.resolution == "unknown"  # closed is not resolved
    assert len(discussion.author_response_source_ids) == 1
    assert "both success and failure" in discussion.reason
    assert discussion.scoring_eligible is False
    assert any(s.kind == "comment" and "issuecomment-9" in s.url for s in result.sources)


def test_maintainer_is_not_automatically_paper_author(monkeypatch):
    reply = {
        "body": "Please retry training.",
        "html_url": "https://github.com/alice/method/issues/1#issuecomment-9",
        "user": {"login": "bob"},
        "author_association": "OWNER",
    }
    network(monkeypatch, issues=[issue("I cannot reproduce results.", count=1)], comments=[reply])
    result = check_code(context())
    assert result.checks.discussions[0].author_response_source_ids == []
    assert any("Account bob" in s.association_reason for s in result.sources)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("I successfully reproduced the results.", "success_reported"),
        ("We have replicated the benchmark.", "success_reported"),
        ("I could not reproduce the results.", "failure_reported"),
        ("The dataset is unavailable.", "failure_reported"),
        ("Can the results be reproduced?", "discussion_only"),
        ("If I reproduced the result, would that be enough?", "discussion_only"),
        ("I have not reproduced the results.", "discussion_only"),
    ],
)
def test_conservative_feedback_classification(text, expected):
    assert classify_feedback(text) == expected


@pytest.mark.parametrize("status", ["timeout", "rate_limited", "not_found", "access_denied", "error"])
def test_network_failure_is_not_absence_or_not_applicable(monkeypatch, status):
    network(monkeypatch, overrides={API: response(API, status=status)})
    result = check_code(context())
    assert result.checks.code_availability == "unable_to_verify"
    assert result.checks.reproduction_signal == "unable_to_verify"
    assert result.dimension.applicability != "not_applicable"
    assert result.sources[0].fetch_status == status


def test_missing_comments_cannot_produce_no_feedback(monkeypatch):
    url = API + "/issues/1/comments?per_page=100&page=1"
    network(
        monkeypatch,
        issues=[issue("installation", count=1, title="Help")],
        overrides={url: response(url, status="timeout")},
    )
    assert check_code(context()).checks.reproduction_signal == "unable_to_verify"


def test_pagination_and_pull_request_filter(monkeypatch):
    first = API + "/issues?state=all&sort=updated&per_page=20&page=1"
    second = first[:-1] + "2"
    called = network(
        monkeypatch,
        overrides={
            first: response(first, [issue("I reproduced the results", pull_request={})], next_page=True),
            second: response(second, [issue("I reproduced the results")]),
        },
    )
    result = check_code(context())
    assert second in called and len(result.checks.discussions) == 1
    assert result.checks.reproduction_signal == "success_reported"


def test_truncated_issue_listing_cannot_prove_absence(monkeypatch):
    first = API + "/issues?state=all&sort=updated&per_page=20&page=1"
    second = first[:-1] + "2"
    network(
        monkeypatch,
        overrides={first: response(first, [], next_page=True), second: response(second, [], next_page=True)},
    )
    result = check_code(context())
    assert result.checks.reproduction_signal == "unable_to_verify"
    assert any("Pagination limit" in s for s in result.checks.scope_limitations)


def test_explicit_theory_differs_from_missing_parse(monkeypatch):
    monkeypatch.setattr(code_checks.LOOKUP, "get", lambda url: pytest.fail("no network expected"))
    ctx = context("This paper is purely theoretical.")
    theory = check_code(ctx)
    assert theory.dimension.applicability == "not_applicable"
    assert theory.dimension.score_status == "not_applicable"
    assert theory.evidence[0].paper_locator.chunk_id == "chunk1"
    ctx.doc = None
    missing = check_code(ctx)
    assert missing.dimension.check_status == "not_run"
    assert missing.dimension.applicability == "undetermined"


def test_theory_word_alone_does_not_exempt_experiments(monkeypatch):
    network(monkeypatch)
    result = check_code(context("This paper is purely theoretical. We conduct experiments."))
    assert result.dimension.applicability != "not_applicable"


def test_official_page_repository_discovery(monkeypatch):
    url = "https://example.org/project"
    network(
        monkeypatch,
        overrides={url: response(url, text=f'<h1>{TITLE}</h1><a href="https://github.com/alice/method">Code</a>')},
    )
    result = check_code(context(f"Our code is available at {url}."))
    assert result.checks.code_availability == "available"
    assert any(s.kind == "project_page" for s in result.sources)


def test_http_cache_rate_limit_and_timeout(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/timeout":
            raise httpx.ReadTimeout("timeout")
        if request.url.path == "/limited":
            return httpx.Response(403, headers={"x-ratelimit-remaining": "0"})
        if request.url.path == "/invalid":
            return httpx.Response(200, text="not json", headers={"content-type": "application/json"})
        return httpx.Response(200, json={"ok": True})

    original = httpx.Client
    monkeypatch.setattr(
        code_lookup.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handler), **kw)
    )
    lookup = PublicCodeLookup(interval=0)
    monkeypatch.setattr(lookup, "_public", lambda url: True)
    assert lookup.get("https://example.org/ok").status == "ok"
    cached = lookup.get("https://example.org/ok")
    assert cached.cached and len(calls) == 1
    assert lookup.get("https://example.org/timeout").status == "timeout"
    assert lookup.get("https://example.org/invalid").status == "error"
    assert lookup.get("https://example.org/limited").status == "rate_limited"
    count = len(calls)
    assert lookup.get("https://example.org/new").status == "rate_limited" and len(calls) == count


def test_large_recursive_tree_is_read_but_other_large_payloads_stay_capped(monkeypatch):
    big = {"tree": [{"path": f"data/file_{i:06d}.json", "type": "blob"} for i in range(40_000)]}

    def handler(request):
        return httpx.Response(200, json=big)

    original = httpx.Client
    monkeypatch.setattr(
        code_lookup.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handler), **kw)
    )
    lookup = PublicCodeLookup(interval=0)
    monkeypatch.setattr(lookup, "_public", lambda url: True)
    tree = lookup.get(API + f"/git/trees/{SHA}?recursive=1")
    assert tree.status == "ok" and len(tree.data["tree"]) == 40_000
    assert lookup.get(API + "/contents/data.json?ref=" + SHA).status == "error"


@pytest.mark.parametrize(
    "url",
    [
        "http://example.org",
        "https://localhost",
        "https://127.0.0.1",
        "file:///tmp/a",
        "https://user:pass@example.org",
        "https://example.org:8080",
    ],
)
def test_private_and_unsupported_urls_blocked(url):
    assert not PublicCodeLookup._public(url)


def test_pdf_wrapped_url_is_recovered_only_with_original_span(monkeypatch):
    network(monkeypatch)
    ctx = context("Our code is available at: https://github.com/\n alice/method")
    result = check_code(ctx)
    assert result.checks.code_availability == "available"
    paper = next(e for e in result.evidence if e.origin == "paper")
    assert paper.excerpt in ctx.doc.blocks[0].text
    assert "github.com/\n alice/method" in paper.excerpt


def test_nearby_baseline_url_is_not_our_repository(monkeypatch):
    called = network(monkeypatch, readme="Baseline code")
    ctx = context("Our code is available on request. Baseline: https://github.com/alice/method.")
    result = check_code(ctx)
    assert result.checks.repositories[0].association == "uncertain"
    assert not any("/issues" in url for url in called)


def test_shared_repository_thread_cannot_be_assigned_to_this_paper(monkeypatch):
    readme = "Official implementation for https://arxiv.org/abs/2401.12345 and https://arxiv.org/abs/2301.11111"
    network(monkeypatch, readme=readme, issues=[issue("I reproduced the results.")])
    result = check_code(context())
    assert result.checks.discussions[0].relevance == "undetermined"
    assert result.checks.reproduction_signal == "unable_to_verify"


def test_shared_repository_explicit_paper_reference_is_relevant(monkeypatch):
    readme = "Official implementation for https://arxiv.org/abs/2401.12345 and https://arxiv.org/abs/2301.11111"
    network(monkeypatch, readme=readme, issues=[issue("I reproduced the results of arxiv:2401.12345.")])
    result = check_code(context())
    assert result.checks.discussions[0].relevance == "relevant"
    assert result.checks.reproduction_signal == "success_reported"


def test_existing_task_api_contains_code_check_and_polling_does_not_refetch(monkeypatch):
    from fastapi.testclient import TestClient

    from app.credibility import shell
    from app.main import app

    called = network(monkeypatch)
    monkeypatch.setattr(shell, "build_context", lambda *args, **kwargs: context())
    with TestClient(app) as client:
        created = client.post(
            "/v1/credibility/assessments",
            json={
                "schema_version": "credibility-0.1.0-draft",
                "analysis_mode": "credibility",
                "source": {"type": "arxiv", "arxiv_id": "2401.12345", "upload_id": None},
                "model_id": "not_called",
                "rubric_version": "0.1-draft",
            },
        )
        assert created.status_code == 202
        assert created.json()["status"] == "queued"
        count = len(called)
        result = client.get("/v1/credibility/assessments/" + created.json()["assessment_id"]).json()
        assert result["status"] == "completed"
        assert result["report"]["checks"]["code"]["code_availability"] == "available"
        assert result["report"]["overall"]["score"] is None
        assert len(called) == count


def test_redirect_to_private_host_is_blocked_and_body_size_is_bounded(monkeypatch):
    requested = []

    def handler(request):
        requested.append(str(request.url))
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"location": "https://127.0.0.1/secret"})
        return httpx.Response(200, text="a" * 100)

    original = httpx.Client
    monkeypatch.setattr(
        code_lookup.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handler), **kw)
    )
    lookup = PublicCodeLookup(interval=0, max_bytes=10)
    monkeypatch.setattr(lookup, "_public", lambda url: url.startswith("https://example.org/"))
    assert lookup.get("https://example.org/redirect").status == "access_denied"
    assert not any("127.0.0.1" in url for url in requested)
    assert lookup.get("https://example.org/big").status == "error"


def test_project_page_baseline_link_does_not_confirm_repository(monkeypatch):
    url = "https://example.org/project"
    network(
        monkeypatch,
        readme="An unrelated baseline",
        overrides={url: response(url, text=f'<h1>{TITLE}</h1><a href="https://github.com/alice/method">Baseline</a>')},
    )
    result = check_code(context(f"Our project page: {url}."))
    assert result.checks.repositories[0].association == "uncertain"


def test_dataset_and_code_project_statement_and_nested_unquoted_anchor(monkeypatch):
    url = "https://example.org/geofar"
    page = f"<h1>{TITLE}</h1><a href=//github.com/alice/method><svg><title>Icon</title></svg><span>Code</span></a>"
    network(monkeypatch, overrides={url: response(url, text=page)})
    result = check_code(context(f"The dataset and code are available at {url}."))
    assert result.checks.code_availability == "available"
    assert result.dimension.check_status == "completed" and not result.checks.scope_complete
    assert any("<a href=//github.com" in e.excerpt for e in result.evidence)


def test_project_baselines_heading_code_anchor_does_not_bind_repository(monkeypatch):
    url = "https://example.org/project"
    page = f"<h1>{TITLE}</h1><h2>Baselines</h2><a href=https://github.com/alice/method><span>Code</span></a>"
    network(monkeypatch, readme="An unrelated method", overrides={url: response(url, text=page)})
    result = check_code(context(f"The dataset and code are available at {url}."))
    assert result.checks.code_availability != "available"
    assert result.checks.repositories[0].association == "uncertain"


@pytest.mark.parametrize("ignored", ["vendor/model.py", "third_party/model.py", "docs/example.py", "tutorial/train.py"])
def test_tutorial_vendor_only_tree_is_not_implementation_evidence(monkeypatch, ignored):
    network(
        monkeypatch, overrides={API + f"/git/trees/{SHA}?recursive=1": {"tree": [{"path": ignored, "type": "blob"}]}}
    )
    result = check_code(context())
    assert result.checks.code_availability == "unable_to_verify"
    assert not result.checks.repositories[0].implementation_evidence_ids
    assert result.dimension.check_status == "completed"


def test_explicit_sha_and_subdirectory_never_fall_back_to_default_head(monkeypatch):
    pinned = "b" * 40
    readme = base64.b64encode(
        f"# {TITLE}\nOfficial implementation.\npython train.py\nDownload dataset and weights.".encode()
    ).decode()
    calls = network(
        monkeypatch,
        overrides={
            API + "/commits/" + pinned: {"sha": pinned},
            API + "/readme/paper?ref=" + pinned: {"encoding": "base64", "content": readme},
            API + f"/git/trees/{pinned}?recursive=1": {
                "tree": [
                    {"path": "other/train.py", "type": "blob"},
                    {"path": "paper/train.py", "type": "blob"},
                    {"path": "paper/requirements.txt", "type": "blob"},
                ]
            },
            API + "/contents/paper/train.py?ref=" + pinned: {
                "encoding": "base64",
                "content": base64.b64encode(b"def train(): return 42").decode(),
            },
            API + "/contents/paper/requirements.txt?ref=" + pinned: {
                "encoding": "base64",
                "content": base64.b64encode(b"torch==2.0").decode(),
            },
        },
    )
    result = check_code(context(f"Our code is available at https://github.com/alice/method/tree/{pinned}/paper."))
    repo = result.checks.repositories[0]
    assert repo.inspected_revision == pinned and result.checks.code_availability == "available"
    state = result.followup_state[repo.repository_id]
    assert state["requested_revision"] == pinned and state["inspected_subdir"] == "paper"
    assert API + "/commits/main" not in calls
    from app.credibility.snapshots import read_snapshot

    summaries = [
        read_snapshot(source.snapshot_ref).normalized_json
        for source in result.sources
        if source.association_reason.startswith("Source-bound implementation")
    ]
    assert summaries[0]["implementation_paths"] == ["paper/train.py"]
    assert summaries[0]["dependency_manifest_paths"] == ["paper/requirements.txt"]
    assert summaries[0]["inspected_files"][0]["path"] == "paper/train.py"
    assert summaries[0]["runnable_status"] == "not_executed"
    assert summaries[0]["data_weights_setup_notes"]


def test_slash_branch_longest_prefix_preserves_scope(monkeypatch):
    branch = "experiment/v2"
    overrides = {
        API + "/git/matching-refs/heads/experiment": [
            {"ref": "refs/heads/experiment"},
            {"ref": "refs/heads/experiment/v2"},
        ],
        API + "/git/matching-refs/tags/experiment": [],
        API + "/commits/experiment%2Fv2": {"sha": SHA},
        API + "/readme/paper?ref=" + SHA: {
            "encoding": "base64",
            "content": base64.b64encode(f"Official implementation of {TITLE}".encode()).decode(),
        },
        API + f"/git/trees/{SHA}?recursive=1": {"tree": [{"path": "paper/train.py", "type": "blob"}]},
        API + "/contents/paper/train.py?ref=" + SHA: {
            "encoding": "base64",
            "content": base64.b64encode(b"def train(): return 42").decode(),
        },
    }
    calls = network(monkeypatch, overrides=overrides)
    result = check_code(context("Our code is available at https://github.com/alice/method/tree/experiment/v2/paper."))
    state = result.followup_state["code_repo_1"]
    assert state["requested_revision"] == branch and state["inspected_subdir"] == "paper"
    assert result.checks.code_availability == "available"
    assert API + "/commits/main" not in calls


def test_cited_subdir_cannot_use_other_paper_implementation(monkeypatch):
    pinned = "b" * 40
    network(
        monkeypatch,
        overrides={
            API + "/commits/" + pinned: {"sha": pinned},
            API + "/readme/ours?ref=" + pinned: {
                "encoding": "base64",
                "content": base64.b64encode(f"Official implementation of {TITLE}".encode()).decode(),
            },
            API + f"/git/trees/{pinned}?recursive=1": {"tree": [{"path": "other/train.py", "type": "blob"}]},
        },
    )
    result = check_code(context(f"Our code is available at https://github.com/alice/method/tree/{pinned}/ours."))
    assert result.checks.code_availability == "unable_to_verify"


def test_semantic_confirmation_completes_without_replaying_metadata_and_is_idempotent(monkeypatch):
    from app.credibility.code_checks import complete_confirmed_repositories

    calls = network(monkeypatch, readme="Implementation details without an explicit paper title")
    result = check_code(context("Related repository: https://github.com/alice/method."))
    assert result.checks.repositories[0].association == "uncertain"
    previous = list(calls)
    old_sources = [source.source_id for source in result.sources]
    old_evidence = [evidence.evidence_id for evidence in result.evidence]
    result.checks.repositories[0].association = "confirmed"  # Simulate an already validated model judgment.
    completed = complete_confirmed_repositories(context(), result)
    assert completed.checks.code_availability == "available"
    assert completed.external_request_count == len(calls)
    assert API not in calls[len(previous) :]
    assert not any("/readme" in url or "/commits/" in url for url in calls[len(previous) :])
    assert [source.source_id for source in completed.sources[: len(old_sources)]] == old_sources
    assert [evidence.evidence_id for evidence in completed.evidence[: len(old_evidence)]] == old_evidence
    before = list(calls)
    again = complete_confirmed_repositories(context(), completed)
    assert calls == before and again.external_request_count == completed.external_request_count
    assert again.model_dump() == completed.model_dump()


def test_followup_respects_combined_external_budget_and_is_partial(monkeypatch):
    from app.credibility.code_checks import complete_confirmed_repositories

    calls = network(monkeypatch, readme="Implementation details")
    result = check_code(context("Related repository: https://github.com/alice/method."))
    result.checks.repositories[0].association = "confirmed"
    result.external_request_count = 40
    before = list(calls)
    completed = complete_confirmed_repositories(context(), result)
    assert calls == before and completed.external_request_count == 40
    assert completed.dimension.check_status == "partial"
    assert completed.checks.code_availability == "unable_to_verify"
    assert any("budget exhausted" in value for value in completed.checks.scope_limitations)


def test_bounded_issue_pagination_gap_is_not_a_failed_plan(monkeypatch):
    first = API + "/issues?state=all&sort=updated&per_page=20&page=1"
    second = first[:-1] + "2"
    network(
        monkeypatch,
        overrides={first: response(first, [], next_page=True), second: response(second, [], next_page=True)},
    )
    result = check_code(context())
    assert result.dimension.check_status == "completed"
    assert not result.checks.scope_complete and result.checks.reproduction_signal == "unable_to_verify"


def test_real_github_base64_line_breaks_are_decoded_for_readme_and_files(monkeypatch):
    def wrapped(value):
        encoded = base64.b64encode(value).decode()
        return "\n".join(encoded[index : index + 60] for index in range(0, len(encoded), 60)) + "\n"

    network(
        monkeypatch,
        overrides={
            API + "/readme?ref=" + SHA: {
                "encoding": "base64",
                "content": wrapped(
                    (f"# {TITLE}\nOfficial implementation.\nDownload the dataset and weights.\n" * 3).encode()
                ),
            },
            API + "/contents/train.py?ref=" + SHA: {
                "encoding": "base64",
                "content": wrapped(b"def train():\n    return 42\n" * 3),
            },
        },
    )
    result = check_code(context())
    assert result.dimension.check_status == "completed"
    assert any("def train()" in evidence.excerpt for evidence in result.evidence)
    assert result.followup_state["code_repo_1"]["readme_readable"]


def test_failed_sample_read_is_explicit_in_aggregate_inspection(monkeypatch):
    from app.credibility.snapshots import read_snapshot

    network(
        monkeypatch,
        overrides={
            API + "/contents/train.py?ref=" + SHA: response(API + "/contents/train.py?ref=" + SHA, status="timeout")
        },
    )
    result = check_code(context())
    summaries = [
        read_snapshot(source.snapshot_ref).normalized_json
        for source in result.sources
        if source.association_reason.startswith("Source-bound implementation")
    ]
    assert result.dimension.check_status == "partial"
    assert summaries[0]["inspection_status"] == "partial"
    assert summaries[0]["inspected_files"][0]["fetch_status"] == "timeout"
    assert not summaries[0]["inspected_files"][0]["content_read"]


def test_explicit_sha_mismatching_response_is_not_inspected(monkeypatch):
    pinned = "b" * 40
    calls = network(monkeypatch, overrides={API + "/commits/" + pinned: {"sha": SHA}})
    result = check_code(context(f"Our code is available at https://github.com/alice/method/tree/{pinned}/ours."))
    assert result.checks.code_availability == "unable_to_verify"
    assert result.checks.repositories[0].inspected_revision is None
    assert not any("/git/trees/" in url for url in calls)
    assert result.dimension.check_status == "partial"


def test_project_explicit_scope_overrides_earlier_unbound_root_candidate(monkeypatch):
    pinned = "b" * 40
    url = "https://example.org/project"
    readme = base64.b64encode(f"Official implementation of {TITLE}".encode()).decode()
    calls = network(
        monkeypatch,
        overrides={
            url: response(
                url, text=f"<h1>{TITLE}</h1><a href=https://github.com/alice/method/tree/{pinned}/paper>Code</a>"
            ),
            API + "/commits/" + pinned: {"sha": pinned},
            API + "/readme/paper?ref=" + pinned: {"encoding": "base64", "content": readme},
            API + f"/git/trees/{pinned}?recursive=1": {"tree": [{"path": "paper/train.py", "type": "blob"}]},
            API + "/contents/paper/train.py?ref=" + pinned: {
                "encoding": "base64",
                "content": base64.b64encode(b"def train(): return 42").decode(),
            },
        },
    )
    result = check_code(context(f"Related: https://github.com/alice/method. Our project page: {url}."))
    assert result.checks.code_availability == "available"
    assert result.followup_state["code_repo_1"]["inspected_subdir"] == "paper"
    assert result.checks.repositories[0].inspected_revision == pinned
    assert API + "/commits/main" not in calls


def test_empty_source_file_tree_is_not_implementation(monkeypatch):
    network(
        monkeypatch,
        overrides={API + f"/git/trees/{SHA}?recursive=1": {"tree": [{"path": "train.py", "type": "blob", "size": 0}]}},
    )
    result = check_code(context())
    assert result.checks.code_availability == "unable_to_verify"


@pytest.mark.parametrize("missing_document", [True, False])
def test_semantic_not_needed_cannot_complete_a_missing_input_code_check(monkeypatch, missing_document):
    from app.config import get_settings
    from app.credibility.code_checks import complete_confirmed_repositories
    from app.credibility.execution import _result_state, check_code_with_model

    ctx = context()
    if missing_document:
        ctx.doc = None
    else:
        ctx.doc.blocks = []
    ctx.model_id = "model_standard_v1"
    monkeypatch.setattr(get_settings(), "credibility_code_model_enabled", True)
    monkeypatch.setattr("app.pipeline.orchestrator.build_llm_client", lambda model_id: object())
    monkeypatch.setattr(code_checks.LOOKUP, "get", lambda url: pytest.fail("Missing input cannot fetch a repository"))
    base = check_code(ctx)
    assert base.dimension.check_status == "not_run"
    direct = complete_confirmed_repositories(ctx, base)
    assert direct.model_dump() == base.model_dump() and direct is not base
    result = check_code_with_model(ctx)
    assert result.dimension.check_status == "not_run"
    assert _result_state(result) == "partial"
    assert result.external_request_count == 0 and not result.model_calls
    assert not result.sources and not result.checks.repositories


def test_missing_input_followup_preserves_failed_producer_state():
    from app.credibility.code_checks import complete_confirmed_repositories

    ctx = context()
    ctx.doc = None
    base = check_code(ctx)
    base.dimension.check_status = "failed"
    assert complete_confirmed_repositories(ctx, base).dimension.check_status == "failed"


def test_baseline_supported_models_table_does_not_make_single_paper_repository_shared(monkeypatch):
    readme = f"""# {TITLE}
Official implementation for this paper.

## Introduction
A model for regional climate fields.

## Supported Models
| Model | Paper |
|---|---|
| FFL | https://arxiv.org/abs/2012.12821 |
| SwinIR | https://arxiv.org/abs/2108.10257 |
| Baseline | https://arxiv.org/abs/2305.14452 |

## References
https://arxiv.org/abs/2404.17752
"""
    network(monkeypatch, readme=readme, issues=[issue("I reproduced the results on CERRA 2x.")])
    result = check_code(context())
    assert result.followup_state["code_repo_1"]["shared_paper_ids"] == []
    assert result.checks.discussions[0].relevance == "relevant"
    assert result.checks.reproduction_signal == "success_reported"


def test_official_multi_paper_intro_bullets_remain_shared(monkeypatch):
    readme = """# Tangent model toolkit
Official code repository for
- Paper A https://arxiv.org/abs/2401.12345
- Paper B https://arxiv.org/abs/2301.11111

### Requirements
Python and Torch.

### Citing dependencies
https://arxiv.org/abs/2201.99999
"""
    network(monkeypatch, readme=readme, issues=[issue("I reproduced the results.")])
    result = check_code(context())
    assert result.followup_state["code_repo_1"]["shared_paper_ids"] == ["2301.11111", "2401.12345"]
    assert result.checks.discussions[0].relevance == "undetermined"
    assert result.checks.reproduction_signal == "unable_to_verify"


def test_explicit_supported_papers_section_excludes_supported_models():
    from app.credibility.code_repository_scope import supported_paper_ids

    text = """# Library
## Supported Papers
- https://arxiv.org/abs/2401.12345
- https://arxiv.org/abs/2301.11111
## Supported Models
https://arxiv.org/abs/2201.99999
"""
    assert supported_paper_ids(text) == ["2301.11111", "2401.12345"]


def test_readme_command_resolves_observed_nonstandard_experiment_entrypoint(monkeypatch):
    from app.credibility.snapshots import read_snapshot

    path = "experiments/regional_cerra_downscaling.py"
    readme = f"# {TITLE}\nOfficial implementation.\n## Training\n```bash\npython -u {path} --scale 2\n```\n"
    network(
        monkeypatch,
        readme=readme,
        overrides={
            API + f"/git/trees/{SHA}?recursive=1": {"tree": [{"path": path, "type": "blob"}]},
            API + "/contents/" + path + "?ref=" + SHA: {
                "encoding": "base64",
                "content": base64.b64encode(b"import argparse\nparser = argparse.ArgumentParser()\n").decode(),
            },
        },
    )
    result = check_code(context())
    facts = [
        read_snapshot(source.snapshot_ref).normalized_json
        for source in result.sources
        if source.association_reason.startswith("Source-bound implementation")
    ][0]
    assert facts["entrypoint_paths"] == [path]
    assert facts["inspected_files"][0]["path"] == path
    assert facts["runnable_status"] == "not_executed"


def test_readme_commands_cannot_invent_files_or_escape_scope():
    from app.credibility.code_repository_scope import readme_entrypoints

    readme = "python missing.py\npython ../other/train.py\nbash scripts/run.sh\npython -m experiments.real\n"
    assert readme_entrypoints(readme, ["paper/scripts/run.sh", "paper/experiments/real.py"], "paper") == [
        "paper/scripts/run.sh",
        "paper/experiments/real.py",
    ]


def test_own_paper_label_does_not_authorize_later_backbone_paragraphs(monkeypatch):
    readme = f"""# {TITLE}
This repo contains the source code of a Python package and examples.
See our paper for details.

**{TITLE}** <br>
*Ada Example and Milo Researcher* <br>
Paper: https://arxiv.org/abs/2401.12345 <br>
Video: https://example.org/video

We obtain results using [Backbone A](https://arxiv.org/abs/2301.11111)
and [Backbone B](https://arxiv.org/abs/2201.22222).

| Our method | Comparison |
|---|---|
| result | [Adapter](https://arxiv.org/abs/2101.33333) |

On another model we compare with [prefix tuning](https://arxiv.org/abs/2001.44444).
## Repository Overview
Implementation directories.
"""
    network(monkeypatch, readme=readme, issues=[issue("I reproduced the results.")])
    result = check_code(context())
    assert result.followup_state["code_repo_1"]["shared_paper_ids"] == ["2401.12345"]
    assert result.checks.discussions[0].relevance == "relevant"


def test_official_relation_grants_only_adjacent_link_list_not_later_paragraph():
    from app.credibility.code_repository_scope import supported_paper_ids

    readme = """# Toolkit
Official code repository for
- Paper A https://arxiv.org/abs/2401.12345
- Paper B https://arxiv.org/abs/2301.11111

We use a transformer from https://arxiv.org/abs/2201.22222.
"""
    assert supported_paper_ids(readme) == ["2301.11111", "2401.12345"]


@pytest.mark.parametrize(
    "payload",
    [
        {"encoding": "base64", "content": "$not-base64"},
        {"encoding": "none", "content": ""},
    ],
)
def test_http200_readme_payload_failure_cannot_be_erased_by_followup(monkeypatch, payload):
    from app.credibility.code_checks import complete_confirmed_repositories

    calls = network(monkeypatch, overrides={API + "/readme?ref=" + SHA: payload})
    result = check_code(context())
    assert result.dimension.check_status == "partial"
    assert all(source.fetch_status == "ok" for source in result.sources)
    assert result.followup_state["code_repo_1"]["inspection_status"] == "partial"
    before = list(calls)
    completed = complete_confirmed_repositories(context(), result)
    assert calls == before
    assert completed.dimension.check_status == "partial"
    assert completed.followup_state["code_repo_1"]["inspection_status"] == "partial"


@pytest.mark.parametrize("payload", [{"tree": "corrupt"}, {}, {"tree": [None]}])
def test_http200_tree_payload_failure_cannot_be_erased_by_followup(monkeypatch, payload):
    from app.credibility.code_checks import complete_confirmed_repositories

    calls = network(monkeypatch, overrides={API + f"/git/trees/{SHA}?recursive=1": payload})
    result = check_code(context())
    assert result.dimension.check_status == "partial"
    assert all(source.fetch_status == "ok" for source in result.sources)
    before = list(calls)
    assert complete_confirmed_repositories(context(), result).dimension.check_status == "partial"
    assert calls == before


def test_setup_only_repository_retains_manifest_without_implementation_availability(monkeypatch):
    from app.credibility.snapshots import read_snapshot

    network(
        monkeypatch,
        overrides={
            API + f"/git/trees/{SHA}?recursive=1": {"tree": [{"path": "setup.py", "type": "blob", "size": 100}]},
            API + "/contents/setup.py?ref=" + SHA: {
                "encoding": "base64",
                "content": base64.b64encode(b'from setuptools import setup\nsetup(name="package-shell")\n').decode(),
            },
        },
    )
    result = check_code(context())
    assert result.checks.code_availability == "unable_to_verify"
    assert not result.checks.repositories[0].implementation_evidence_ids
    facts = [
        read_snapshot(source.snapshot_ref).normalized_json
        for source in result.sources
        if source.association_reason.startswith("Source-bound implementation")
    ][0]
    assert facts["dependency_manifest_paths"] == ["setup.py"]
    assert facts["implementation_paths"] == [] and facts["inspected_files"][0]["path"] == "setup.py"
    assert result.dimension.check_status == "completed"


@pytest.mark.parametrize(
    "source",
    [
        "# Placeholder only\n",
        '"""Package placeholder."""\n',
        'from external_library import train\n__version__ = "0.1"\n',
    ],
)
def test_sole_observed_initializer_shell_is_not_implementation(monkeypatch, source):
    network(
        monkeypatch,
        overrides={
            API + f"/git/trees/{SHA}?recursive=1": {"tree": [{"path": "pkg/__init__.py", "type": "blob", "size": 100}]},
            API + "/contents/pkg/__init__.py?ref=" + SHA: {
                "encoding": "base64",
                "content": base64.b64encode(source.encode()).decode(),
            },
        },
    )
    result = check_code(context())
    assert result.checks.code_availability == "unable_to_verify"
    assert not result.checks.repositories[0].implementation_evidence_ids
    assert result.dimension.check_status == "completed"


@pytest.mark.parametrize(
    "source",
    [
        "def low_rank_update(a, b):\n    return a @ b\n",
        "from .factory import build_layers\n__all__ = [build_layers()]\n",
    ],
)
def test_meaningful_initializer_remains_an_implementation(monkeypatch, source):
    network(
        monkeypatch,
        overrides={
            API + f"/git/trees/{SHA}?recursive=1": {"tree": [{"path": "pkg/__init__.py", "type": "blob", "size": 100}]},
            API + "/contents/pkg/__init__.py?ref=" + SHA: {
                "encoding": "base64",
                "content": base64.b64encode(source.encode()).decode(),
            },
        },
    )
    result = check_code(context())
    assert result.checks.code_availability == "available"
    assert result.checks.repositories[0].implementation_evidence_ids


def test_multifile_inspection_prefers_real_module_over_package_initializer(monkeypatch):
    from app.credibility.snapshots import read_snapshot

    calls = network(
        monkeypatch,
        overrides={
            API + f"/git/trees/{SHA}?recursive=1": {
                "tree": [
                    {"path": "pkg/__init__.py", "type": "blob"},
                    {"path": "pkg/layers.py", "type": "blob"},
                    {"path": "pkg/utils.py", "type": "blob"},
                ]
            },
            API + "/contents/pkg/layers.py?ref=" + SHA: {
                "encoding": "base64",
                "content": base64.b64encode(b"def low_rank_update(a, b):\n    return a @ b\n").decode(),
            },
        },
    )
    result = check_code(context())
    facts = [
        read_snapshot(source.snapshot_ref).normalized_json
        for source in result.sources
        if source.association_reason.startswith("Source-bound implementation")
    ][0]
    assert result.checks.code_availability == "available"
    assert facts["inspected_files"][0]["path"] == "pkg/layers.py"
    assert API + "/contents/pkg/__init__.py?ref=" + SHA not in calls


def test_oversized_recursive_tree_falls_back_to_shallow_source_directories(monkeypatch):
    recursive = API + f"/git/trees/{SHA}?recursive=1"
    called = network(monkeypatch, overrides={
        recursive: response(recursive, status="error"),
        API + f"/git/trees/{SHA}": {"tree": [
            {"path": "README.md", "type": "blob"},
            {"path": "data", "type": "tree", "sha": "d" * 40},
            {"path": "src", "type": "tree", "sha": "s" * 40},
        ]},
        API + "/git/trees/" + "s" * 40: {"tree": [{"path": "train.py", "type": "blob"}]},
        API + "/contents/src/train.py?ref=" + SHA: {
            "encoding": "base64", "content": base64.b64encode(b"def train():\n    return 1\n").decode(),
        },
    })
    result = check_code(context())
    repo = result.checks.repositories[0]
    assert result.checks.code_availability == "available" and repo.implementation_evidence_ids
    assert not any("/git/trees/" + "d" * 40 in url for url in called)  # data directories are skipped
    assert any("one level deep" in note for note in result.checks.scope_limitations)
