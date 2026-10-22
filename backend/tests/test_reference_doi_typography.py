"""DOI typography repair changes lookup hints, never archived source text."""
from __future__ import annotations

import pytest

from app.credibility.reference_extraction import (
    REFERENCE_HINTS_VERSION,
    extract_reference_entries,
    metadata_hints,
)
from app.parsing.base import TextBlock

ACL_RAW = (
    "Linguistics. doi: 10.18653/v1/2020.ﬁndings-emnlp.41. "
    "URL https://aclanthology. org/2020.findings-emnlp.41. 14"
)
HAM_TITLE = "Grassmann discriminant analysis: a unifying view on subspace-based learning"
HAM_RAW = (
    f"Jihun Ham and Daniel D. Lee. {HAM_TITLE}. In ICML, pp. 376–383, 2008. "
    "URL https://doi.org/10.1145/1390156. 1390204."
)


def test_reference_hint_behavior_has_an_explicit_version():
    assert REFERENCE_HINTS_VERSION == "reference-hints-1.4-back-references"


def test_real_ligature_doi_is_complete_and_doi_label_is_not_a_paper_title():
    hints = metadata_hints(ACL_RAW)
    assert hints["doi"] == "10.18653/v1/2020.findings-emnlp.41"
    assert hints["title"] is None
    assert "ﬁndings" in ACL_RAW


def test_real_acm_doi_keeps_article_suffix_and_original_title():
    hints = metadata_hints(HAM_RAW)
    assert hints["doi"] == "10.1145/1390156.1390204"
    assert hints["title"] == HAM_TITLE
    assert hints["authors"] == ["Jihun Ham", "Daniel D. Lee"]
    assert hints["year"] == 2008


@pytest.mark.parametrize("separator", [" ", "\t", "\n", "\r\n", " \n "])
def test_only_complete_acm_numeric_components_can_join_across_typographic_whitespace(separator):
    raw = f"doi: 10.1145/1390156.{separator}1390204."
    assert metadata_hints(raw)["doi"] == "10.1145/1390156.1390204"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("doi:10.1145/1390156. 2008.", "10.1145/1390156"),
        ("doi:10.1145/1390156. pp. 376–383.", "10.1145/1390156"),
        ("doi:10.1145/1390156. 1390204–1390208.", "10.1145/1390156"),
        ("doi:10.1145/1390156. 1390204-1390208.", "10.1145/1390156"),
        ("doi:10.1145/1390156. [2] 1390204. Another citation.", "10.1145/1390156"),
        ("doi:10.1145/1390156.\n\n1390204. Another citation.", "10.1145/1390156"),
        ("doi:10.1145/1390156. 10.1145/1390204.", "10.1145/1390156"),
        ("doi:10.1145/139015. 1390204.", "10.1145/139015"),
        ("doi:10.1145/13901560. 1390204.", "10.1145/13901560"),
        ("doi:10.1145/1390156. 139020.", "10.1145/1390156"),
        ("doi:10.1145/1390156. 13902040.", "10.1145/1390156"),
        ("doi:10.1145/1390156. 1390204a.", "10.1145/1390156"),
        ("doi:10.1145/1390156. 1390204/child.", "10.1145/1390156"),
        ("doi:10.1145/1390156. 1390204.5.", "10.1145/1390156"),
        ("doi:10.5555/1390156. 1390204.", "10.5555/1390156"),
        ("doi:10.1145/1390156a. 1390204.", "10.1145/1390156a"),
    ],
)
def test_numeric_continuation_does_not_consume_years_pages_citations_or_partial_tokens(raw, expected):
    assert metadata_hints(raw)["doi"] == expected


@pytest.mark.parametrize(
    "label", ["doi: 10.18653/v1/2020.ﬁndings-emnlp.41", "doi:",
              "https://doi.org/10.1000/ﬂow", "http://dx.doi.org/10.1000/ﬁne"],
)
def test_doi_only_title_candidates_are_not_used_as_title_conflict_evidence(label):
    assert metadata_hints(f"Linguistics. {label}.")["title"] is None


def test_doi_letters_inside_a_real_title_are_preserved():
    title = "DOI: A study of ﬁne-grained ﬂows"
    raw = f"Ada Author. {title}. 2024. doi:10.1000/ﬁne.ﬂow."
    hints = metadata_hints(raw)
    assert hints["doi"] == "10.1000/fine.flow"
    assert hints["title"] == title
    assert hints["year"] == 2024
    assert hints["authors"] == ["Ada Author"]


def test_extracting_doi_hints_does_not_change_entry_or_original_source_fragments():
    first = TextBlock(
        chunk_id="refs_a", text="[1] Ada Author. A ﬁne ﬂow study. 2024. doi:10.1145/1390156.",
        page_start=12, section_id="refs", section_title="References", section_role="references",
    )
    second = TextBlock(
        chunk_id="refs_b", text="1390204.\n[2] Jane Doe. Another article. 2023. doi:10.1000/ﬁne.",
        page_start=13, section_id="refs", section_title="References", section_role="references",
    )
    original = (first.text, second.text)
    entries, count, gaps = extract_reference_entries([first, second])
    assert count == 2 and not gaps
    entry_text = entries[0].text
    fragments = entries[0].fragments
    hints = metadata_hints(entry_text)
    assert hints["doi"] == "10.1145/1390156.1390204"
    assert hints["title"] == "A ﬁne ﬂow study"
    assert entries[0].text == entry_text and entries[0].fragments == fragments
    assert fragments[0].text == first.text and fragments[1].text == "1390204."
    assert (first.text, second.text) == original
