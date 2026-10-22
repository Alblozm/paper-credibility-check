"""An archived listing cannot lend another thread's body to a named source."""
import json
from types import SimpleNamespace

import pytest

from scripts.audit_code_discovery import quote_documents


def documents(url, rows, kind="issue"):
    helpers = {
        "external_tools": SimpleNamespace(
            _text_from_data=lambda data, **_: json.dumps(data), _html_text=lambda text: text,
        ),
        "code_repository_scope": SimpleNamespace(ProjectHTML=lambda text: SimpleNamespace(text=text)),
    }
    return quote_documents(SimpleNamespace(url=url, kind=kind), SimpleNamespace(normalized_json=rows), helpers)


def test_named_issue_cannot_use_a_different_thread_in_its_parent_listing():
    with pytest.raises(ValueError, match="unique exact target"):
        documents("https://github.com/org/repo/issues/99", [
            {"html_url": "https://github.com/org/repo/issues/1", "body": "I reproduced the result."},
        ])


def test_named_issue_selects_only_its_own_row():
    rows = [{"html_url": "https://github.com/org/repo/issues/1", "body": "unrelated"},
            {"html_url": "https://github.com/org/repo/issues/99", "body": "target"}]
    observed = documents("https://github.com/org/repo/issues/99", rows)
    assert "target" in observed[0] and "unrelated" not in observed[0]


def test_duplicate_target_rows_are_ambiguous():
    row = {"html_url": "https://github.com/org/repo/issues/99", "body": "target"}
    with pytest.raises(ValueError, match="unique exact target"):
        documents(row["html_url"], [row, row])


def test_native_comment_page_remains_bound_to_one_repository_and_thread():
    url = "https://api.github.com/repos/org/repo/issues/99/comments?per_page=100&page=1"
    rows = [{"html_url": "https://github.com/org/repo/issues/99#issuecomment-123", "body": "target"}]
    assert "target" in documents(url, rows, "comment")[0]
    assert json.loads(documents(url, [], "comment")[0]) == []
    for bad in [{"html_url": "https://github.com/org/other/issues/99#issuecomment-123"},
                {"html_url": "https://github.com/org/repo/issues/98#issuecomment-123"},
                {"html_url": "https://github.com/org/repo/issues/99#issuecomment-notanid"}, {}]:
        with pytest.raises(ValueError, match="cross-thread"):
            documents(url, rows + [bad], "comment")
