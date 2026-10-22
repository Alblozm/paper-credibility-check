"""Model link roles replace the 'our code is available' sentence rule; fetching stays deterministic."""
from __future__ import annotations

import json

import pytest

from app.config import get_settings
from app.credibility import code_checks
from app.credibility.code_checks import check_code
from app.credibility.paper_link_classifier import classify_paper_links
from app.llm.base import LLMResponse
from tests.test_code_checks import TITLE, context, network, response

FOOTNOTE = "1https://github.com/alice/method"


@pytest.fixture
def labelled(monkeypatch):
    monkeypatch.setattr(get_settings(), "credibility_paper_link_classifier_enabled", True)
    seen = []

    def use(roles):
        def classify(ctx, links, calls):
            seen.extend(links)
            return {row["link_id"]: roles.get(row["url"], "unclear") for row in links}

        monkeypatch.setattr("app.credibility.paper_link_classifier.classify_paper_links", classify)
        return seen

    return use


def _ctx(text):
    ctx = context(text)
    ctx.model_id = "model_standard_v1"
    return ctx


def test_sentence_rules_leave_a_bare_footnote_link_uncertain(monkeypatch):
    network(monkeypatch, readme="# Another title\nA benchmark.")
    result = check_code(_ctx(FOOTNOTE))
    assert result.checks.repositories[0].association == "uncertain"


def test_model_labelled_footnote_link_is_the_papers_own_repository(monkeypatch, labelled):
    seen = labelled({"https://github.com/alice/method": "own_code"})
    network(monkeypatch, readme="# Another title\nA benchmark.")
    result = check_code(_ctx(FOOTNOTE))
    repo = result.checks.repositories[0]
    assert repo.association == "confirmed" and repo.implementation_evidence_ids
    assert result.checks.code_availability == "available"
    assert seen[0]["url"] == "https://github.com/alice/method" and seen[0]["text_before"] == "1"


def test_model_labelled_project_page_is_followed_to_its_code_link(monkeypatch, labelled):
    page = "https://alice.github.io/method"
    labelled({page: "own_project_page"})
    network(monkeypatch, overrides={
        page: response(page, text=f'<h1>{TITLE}</h1><a href="https://github.com/alice/method">Code</a>'),
    })
    result = check_code(_ctx(f"Alice Smith\nUniversity\n{page}"))
    assert result.checks.code_availability == "available"
    assert any(s.kind == "project_page" for s in result.sources)


@pytest.mark.parametrize("role", ["third_party", "own_data_or_model", "unclear"])
def test_other_roles_do_not_claim_ownership_even_with_code_wording(monkeypatch, labelled, role):
    labelled({"https://github.com/alice/method": role})
    called = network(monkeypatch, readme="Baseline code")
    result = check_code(_ctx("Our code is available at https://github.com/alice/method."))
    assert result.checks.repositories[0].association == "uncertain"
    assert not any("/issues" in url for url in called)


def test_unavailable_model_keeps_the_sentence_rules(monkeypatch):
    monkeypatch.setattr(get_settings(), "credibility_paper_link_classifier_enabled", True)
    monkeypatch.setattr("app.credibility.paper_link_classifier.classify_paper_links",
                        lambda ctx, links, calls: None)
    network(monkeypatch)
    result = check_code(_ctx("Our code is available at https://github.com/alice/method."))
    assert result.checks.repositories[0].association == "confirmed"


class _Client:
    model_version = "offline-model"

    def __init__(self, text):
        self.text, self.prompts = text, []

    def complete_json(self, system, user, **kwargs):
        self.prompts.append(json.loads(user))
        return LLMResponse(text=self.text, model_id="offline", model_version="offline-model")


def test_classifier_sends_paper_identity_and_records_its_call(monkeypatch):
    client = _Client(json.dumps({"links": [{"link_id": "L1", "role": "own_code", "rationale": "Footnote."}]}))
    monkeypatch.setattr("app.pipeline.orchestrator.build_llm_client", lambda model_id: client)
    ctx = _ctx(FOOTNOTE)
    calls = []
    roles = classify_paper_links(ctx, [{"link_id": "L1", "url": "https://github.com/alice/method"}], calls)
    assert roles == {"L1": "own_code"}
    assert client.prompts[0]["paper"] == {"title": TITLE, "authors": ["Alice Smith"]}
    assert [call.purpose for call in calls] == ["paper_link_classification"]


def test_model_call_is_reported_with_the_code_result(monkeypatch):
    monkeypatch.setattr(get_settings(), "credibility_paper_link_classifier_enabled", True)
    client = _Client(json.dumps({"links": [{"link_id": "L1", "role": "own_code", "rationale": "Footnote."}]}))
    monkeypatch.setattr("app.pipeline.orchestrator.build_llm_client", lambda model_id: client)
    network(monkeypatch, readme="# Another title\nA benchmark.")
    result = check_code(_ctx(FOOTNOTE))
    assert [call.purpose for call in result.model_calls] == ["paper_link_classification"]
    assert code_checks.CODE_CHECKS_VERSION == "code-checks-2.6-model-paper-links"
