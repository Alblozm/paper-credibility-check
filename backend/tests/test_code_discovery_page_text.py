"""Readable Fetch prose retains immutable provider text and strict quote scope."""

import json
from pathlib import Path

import httpx
import pytest

from app.credibility.code_checks import check_code
from app.credibility.code_discovery import (
    PAGE_TEXT_VERSION,
    DiscoverySession,
    _Judgments,
    normalize_discovery_page_text,
    safe_candidate_url,
)
from app.credibility.code_discovery_provider import DeepSeekCodeDiscoveryProvider
from app.credibility.code_lookup import now
from app.credibility.context import CheckContext
from app.credibility.external_tools import ExternalToolSession
from app.credibility.snapshots import read_snapshot, verify_report_snapshots
from app.parsing.base import ParsedDocument, TextBlock
from app.schemas.enums import ParseQuality
from app.schemas.paper import Paper

TITLE = "DataMIL: Selecting Data for Robot Imitation Learning with Datamodels"
REPO = "https://github.com/UT-Austin-RobIn/datamil"
PAGE = "https://datamil.example/paper"
RAW = (r"Official implementation of DataMIL\: Selecting Data for Robot Imitation Learning with Datamodels\."
       "\n" r"\[Code\]\(https\:\/\/github.com\/UT\-Austin\-RobIn\/datamil\) Contact a\@example\.org\.")
DISPLAY = (f"Official implementation of {TITLE}.\n"
           + r"\[Code\]" + f"({REPO}) Contact a@example.org.")


@pytest.mark.parametrize("raw,expected", [
    (r"DataMIL\: title", "DataMIL: title"),
    (r"https\:\/\/github.com\/UT\-Austin\-RobIn\/datamil", REPO),
    (r"a\@example\.org", "a@example.org"),
    # Escaped bracket labels are ambiguous with TeX display math. Preserve
    # those brackets while decoding the URL; plain HTTPS extraction still works.
    (r"\[Code\]\(https\:\/\/github.com\/UT\-Austin\-RobIn\/datamil\)", r"\[Code\]" + f"({REPO})"),
    (r"\# Heading\n\*\*Official code\*\*", r"# Heading\n**Official code**"),
])
def test_provider_prose_punctuation_and_escaped_link_delimiters(raw, expected):
    assert normalize_discovery_page_text(raw) == expected


@pytest.mark.parametrize("protected", [
    '```python\nx = r"a\\:b\\_c"\n```\n',
    '~~~~regex\na\\:b\\_c\n~~~~\n',
    r"\`\`\`python" + "\n" + r"x = r'a\:b\_c'" + "\n" + r"\`\`\`" + "\n",
    r"`a\:b\_c`", r"\`a\:b\_c\`", "    " + r"a\:b\_c" + "\n",
    r"$x\_i + a\{b\}$", r"$$x\_i + a\{b\}$$",
    r"\$x\_i + a\{b\}\$", r"\$\$x\_i + a\{b\}\$\$",
    r"\(x\_i + a\{b\}\)", r"\[x\_i + a\{b\}\]",
    r"\begin{align}x\_i + a\{b\} \\ y\_j\end{align}",
])
def test_code_and_math_are_unchanged_but_surrounding_prose_is_readable(protected):
    assert normalize_discovery_page_text(r"Title\:" + "\n" + protected + "\n" + r"prose\.") == (
        "Title:\n" + protected + "\nprose."
    )


@pytest.mark.parametrize("protected", [
    '```python\nx = r"a\\:b\\_c"\n',
    r"\`\`\`python" + "\n" + r"x = r'a\:b\_c'" + "\n",
    r"\(x\_i + a\{b\}", r"\[x\_i + a\{b\}",
])
def test_unclosed_code_and_math_remain_conservatively_protected(protected):
    assert normalize_discovery_page_text(protected) == protected


def test_one_pass_does_not_decode_unicode_entities_percent_commands_or_literal_backslashes():
    raw = r"\alpha \n \u002f &amp; %2f \\path \\:"
    assert normalize_discovery_page_text(raw) == raw
    assert "https://" not in normalize_discovery_page_text(r"https\u003a\u002f\u002fprivate.local")


@pytest.mark.parametrize("raw", [
    r"https\:\/\/example.org\/?token\=never-persist",
    r"https\:\/\/example.org\/?api\_key\=never-persist",
    r"https\:\/\/127.0.0.1\/secret",
    r"https\:\/\/example.org\/%250dsecret",
])
def test_decoded_link_is_still_subject_to_url_safety(raw):
    assert safe_candidate_url(normalize_discovery_page_text(raw)) is None


@pytest.fixture
def fetched_session():
    ctx = CheckContext(source={"type": "arxiv", "arxiv_id": "2401.12345v1"}, repo_root=Path("."),
        generated_at=now(), ref=None, unresolved_reason=None, model_id="model_standard_v1",
        doc=ParsedDocument(paper=Paper(paper_id="page_text", title=TITLE, arxiv_id="2401.12345v1",
            authors=["Alice Smith"], parse_quality=ParseQuality.GOOD), blocks=[TextBlock(
                chunk_id="body", section_id="body", section_title="Method", section_role="body",
                page_start=1, page_end=1, text="We evaluate an imitation learning method.")]))

    def handler(request):
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"results": [{"url": PAGE, "title": TITLE, "snippet": "Candidate only"}]})
        return httpx.Response(200, json={"title": TITLE, "content": RAW})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = DeepSeekCodeDiscoveryProvider(api_key="offline-only", client=client,
                                                resolver=lambda _: ["93.184.216.34"])
        session = DiscoverySession(ctx, check_code(ctx), provider)
        session.execute("q1", "search_paper_code", {"query_id": "discovery_query_1"})
        candidate = next(row for row in session.summary.candidates if row.url == PAGE)
        observed = session.execute("f1", "fetch_discovery_page", {"candidate_id": candidate.candidate_id})
        yield ctx, session, candidate, observed


def test_search_fetch_uses_same_normalized_text_for_quote_and_link_registry(fetched_session):
    _, session, _, observed = fetched_session
    assert observed["status"] == "ok" and observed["text"] == DISPLAY
    linked = next(row for row in session.summary.candidates if row.url == REPO)
    judgment = _Judgments.model_validate({"candidates": [{"candidate_id": linked.candidate_id,
        "resource_kind": "official_implementation", "reason": "Exact fetched declaration and actual code link.",
        "citations": [{"source_id": observed["source_id"], "excerpt": DISPLAY}]}], "reason": "bounded"})
    intake = session.apply(judgment)
    assert intake[0]["url"] == REPO and linked.status == "candidate" and linked.repository_id is None
    evidence = next(row for row in session.result.evidence if row.evidence_id == linked.evidence_ids[0])
    assert evidence.excerpt == DISPLAY and evidence.source_id == observed["source_id"]


def test_normalized_child_keeps_original_parent_bytes_and_provenance(fetched_session):
    _, session, _, observed = fetched_session
    child = read_snapshot(observed["snapshot_ref"])
    parent_ref = child.manifest["parent_ref"]
    parent = read_snapshot(parent_ref)
    original = json.dumps(parent.normalized_json, sort_keys=True)
    digest = parent.manifest["blobs"]["normalized_json"]["sha256"]
    assert parent.normalized_json["body"] == RAW and parent.normalized_text == RAW
    assert child.normalized_text == DISPLAY
    assert child.manifest["metadata"]["normalization_version"] == PAGE_TEXT_VERSION
    assert child.manifest["metadata"]["source_id"] == observed["source_id"]
    verify_report_snapshots({"sources": session.result.sources, "code_discovery": session.summary})
    assert json.dumps(read_snapshot(parent_ref).normalized_json, sort_keys=True) == original
    assert read_snapshot(parent_ref).manifest["blobs"]["normalized_json"]["sha256"] == digest


def test_reopening_same_source_returns_the_same_normalized_page_view(fetched_session):
    ctx, session, _, observed = fetched_session
    source = next(row for row in session.result.sources if row.source_id == observed["source_id"])
    reopened = ExternalToolSession(ctx, session.result, max_calls=0).existing_document(source)
    assert DISPLAY in reopened and r"DataMIL\:" not in reopened


@pytest.mark.parametrize("quote", [
    DISPLAY.replace("Official implementation", "Official reproduction"),
    DISPLAY.replace("Datamodels", "Other Models"),
    DISPLAY.replace("Contact a@example.org.", "Contact Alice Smith."),
])
def test_normalization_does_not_admit_reworded_or_fabricated_quotations(fetched_session, quote):
    _, session, candidate, observed = fetched_session
    with pytest.raises(ValueError, match="own_fetched_document"):
        session.apply(_Judgments.model_validate({"candidates": [{"candidate_id": candidate.candidate_id,
            "resource_kind": "official_implementation", "reason": "Fabricated quote must fail.",
            "citations": [{"source_id": observed["source_id"], "excerpt": quote}]}], "reason": "bounded"}))
