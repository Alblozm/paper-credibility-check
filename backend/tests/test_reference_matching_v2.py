"""Typography repairs must not erase author, date or version identity checks."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.credibility.models import CredibilityBibliographicMetadata, CredibilityReferenceCandidate
from app.credibility.reference_matching import VersionLink, classify_reference

_PROOF = "snapshot:v1:" + "a" * 64


def metadata(title="A precise title about language models", authors=None, year=2021, doi=None, arxiv_id=None):
    return CredibilityBibliographicMetadata(
        title=title, authors=["Jane Doe", "John Smith"] if authors is None else authors,
        year=year, doi=doi, arxiv_id=arxiv_id,
    )


def candidate(value=None, *, candidate_id="candidate_1"):
    return CredibilityReferenceCandidate(
        candidate_id=candidate_id, metadata=value or metadata(), source_id="source_1",
        evidence_ids=["evidence_1"], match_basis="Retrieved public metadata.",
    )


@pytest.mark.parametrize(("cited", "found"), [
    ("Adapter- fusion: Non-destructive task composition for transfer learning",
     "AdapterFusion: Non-Destructive Task Composition for Transfer Learning"),
    ("Training verifiably robust neural networks", "Training veriﬁably robust neural networks"),
    ("Large-scale language modeling with mixed precisiontransformers",
     "Large-scale language modeling with mixed precision transformers"),
    ("W ARP: Word-level Adversarial ReProgramming", "WARP: Word-level Adversarial ReProgramming"),
    ("Speech and Language Processing. 2nd edition", "Speech and Language Processing"),
    ("The handbook (second edition)", "The handbook (2nd edition)"),
    ("Research & development", "Research and Development"),
    ("<i>Statistical</i> language models", "Statistical language models"),
])
def test_same_author_and_year_allow_pdf_typography_repairs(cited, found):
    outcome, selected, _ = classify_reference(metadata(cited), [candidate(metadata(found))])
    assert (outcome, selected) == ("matched", "candidate_1")


@pytest.mark.parametrize("authors", [[], ["Sourav Malakar"], ["Jane Other", "John Jones"]])
def test_whitespace_repair_requires_compatible_authors(authors):
    parsed = metadata("Mixed precisiontransformers", authors=authors)
    found = candidate(metadata("Mixed precision transformers"))
    assert classify_reference(parsed, [found])[0] == "unverified"


def test_exact_title_does_not_override_different_authors_in_same_year():
    assert classify_reference(metadata(), [candidate(metadata(authors=["Sourav Malakar"]))])[0] == "unverified"


def test_author_order_initials_and_accent_variants_are_compatible():
    parsed = metadata(authors=["Armen Aghajanyan", "Luke Zettlemoyer", "Sonal Guptá"])
    found = metadata(authors=["A. Aghajanyan", "Gupta, Sonal", "Luke Zettlemoyer"])
    assert classify_reference(parsed, [candidate(found)])[0] == "matched"


def test_author_initial_conflicts_are_rejected_even_for_same_surname():
    parsed = metadata(authors=["Jane Doe"])
    assert classify_reference(parsed, [candidate(metadata(authors=["J. Doe"]))])[0] == "matched"
    assert classify_reference(parsed, [candidate(metadata(authors=["John Doe"]))])[0] == "unverified"
    assert classify_reference(parsed, [candidate(metadata(authors=["Bill Doe"]))])[0] == "unverified"


def test_shared_middle_author_does_not_identify_another_work():
    parsed = metadata(authors=["Jane Doe", "John Smith", "Alice Cooper"])
    found = metadata(authors=["Bill Other", "John Smith", "Different Person"])
    assert classify_reference(parsed, [candidate(found)])[0] == "unverified"


@pytest.mark.parametrize(("parsed_name", "external_name"), [
    ("Leslie G. V aliant", "Leslie G. Valiant"),
    ("L. G. V aliant", "Leslie G. Valiant"),
    ("Shunyu Y ao", "Shunyu Yao"),
    ("Kart hik Narasimhan", "Karthik Narasimhan"),
    ("Elad Ben Zaken", "Elad Ben-Zaken"),
    ("Y unchang Y ang", "Yunchang Yang"),
])
def test_equivalent_author_letters_recover_pdf_spacing_and_compound_surnames(parsed_name, external_name):
    parsed = metadata(authors=[parsed_name])
    found = candidate(metadata(authors=[external_name]))
    assert classify_reference(parsed, [found])[0] == "matched"


@pytest.mark.parametrize(("parsed_name", "external_name"), [
    ("Sophie Hao", "Yiding Hao"), ("Roberta Frank", "Robert Frank"), ("Jane Doe", "John Doe"),
])
def test_author_typography_repairs_do_not_merge_actual_different_given_names(parsed_name, external_name):
    parsed = metadata(authors=[parsed_name])
    found = candidate(metadata(authors=[external_name]))
    assert classify_reference(parsed, [found])[0] == "unverified"


def test_edition_suffix_does_not_merge_two_explicitly_different_editions():
    parsed = metadata("Speech and Language Processing (2nd edition)")
    found = metadata("Speech and Language Processing (3rd edition)")
    assert classify_reference(parsed, [candidate(found)])[0] == "unverified"


def test_high_word_overlap_does_not_accept_an_added_companion_paper_suffix():
    title = "A precise analysis of efficient low rank representations in neural network language models"
    assert classify_reference(metadata(title), [candidate(metadata(title + " companion paper"))])[0] == "unverified"


@pytest.mark.parametrize("year", [2020, 2022, 2025])
def test_matching_title_and_authors_never_implicitly_relax_publication_year(year):
    parsed = metadata("Attention Is All You Need", year=2017)
    found = metadata("Attention Is All You Need", year=year)
    assert classify_reference(parsed, [candidate(found)])[0] == "unverified"


def test_short_titles_are_not_fuzzily_matched():
    assert classify_reference(metadata("Adam"), [candidate(metadata("AdaM2"))])[0] == "unverified"


def test_raw_text_does_not_implicitly_override_wrong_structured_year():
    outcome = classify_reference(metadata(year=2020), [candidate()], raw_text="Correct publication year is 2021")[0]
    assert outcome == "unverified"


def test_direct_doi_match_keeps_existing_title_conflict_semantics():
    parsed = metadata(doi="https://doi.org/10.1234%2FTEST")
    found = candidate(metadata("A completely unrelated scientific investigation", doi="10.1234/test"))
    assert classify_reference(parsed, [found])[:2] == ("metadata_conflict", "candidate_1")


def test_direct_doi_identifier_is_sufficient_when_title_is_not_available():
    parsed = metadata(title=None, doi="doi: 10.1234/TEST.")
    assert classify_reference(parsed, [candidate(metadata(doi="10.1234/test"))])[0] == "matched"


def _version_case(*, changed_title=False, parsed_arxiv="2012.13255"):
    parsed = metadata(year=2020, arxiv_id=parsed_arxiv)
    found = candidate(metadata(
        "An updated publication title" if changed_title else parsed.title, year=2021, doi="10.1234/publication",
    ))
    link = VersionLink(
        cited_arxiv_id="2012.13255", candidate_doi="10.1234/publication", evidence_ref=_PROOF,
        relationship="official-publication-link", cited_title=parsed.title, cited_year=2020,
        cited_authors=tuple(parsed.authors),
    )
    return parsed, found, link


def test_captured_preprint_to_publication_relationship_allows_year_difference():
    parsed, found, link = _version_case()
    outcome, selected, reason = classify_reference(parsed, [found], version_links={found.candidate_id: link})
    assert (outcome, selected) == ("matched", found.candidate_id)
    assert _PROOF in reason


def test_captured_old_title_and_publication_relationship_allow_a_title_change():
    parsed, found, link = _version_case(changed_title=True)
    assert classify_reference(parsed, [found], version_links={found.candidate_id: link})[0] == "matched"
    links = {found.candidate_id: replace(link, cited_title=None)}
    assert classify_reference(parsed, [found], version_links=links)[0] == "unverified"


def test_official_title_authors_and_cited_year_can_recover_missing_preprint_identifier():
    parsed, found, link = _version_case(changed_title=True, parsed_arxiv=None)
    assert classify_reference(parsed, [found], version_links={found.candidate_id: link})[0] == "matched"


@pytest.mark.parametrize("change", [
    {"cited_title": None}, {"cited_title": "A different preprint"},
    {"cited_authors": ()}, {"cited_authors": ("A Different Author",)},
    {"cited_year": None}, {"cited_year": 2021},
])
def test_recovering_missing_preprint_identifier_requires_all_original_metadata(change):
    parsed, found, link = _version_case(parsed_arxiv=None)
    links = {found.candidate_id: replace(link, **change)}
    assert classify_reference(parsed, [found], version_links=links)[0] == "unverified"


@pytest.mark.parametrize("change", [
    {"candidate_doi": "10.1234/another-work"}, {"cited_arxiv_id": "2005.14165"},
    {"cited_arxiv_id": ""}, {"evidence_ref": "https://example.test/uncaptured"},
    {"relationship": "similar-title"}, {"cited_title": "Another preprint title"}, {"cited_year": 2021},
])
def test_mismatched_or_uncaptured_relationship_is_not_version_evidence(change):
    parsed, found, link = _version_case()
    links = {found.candidate_id: replace(link, **change)}
    assert classify_reference(parsed, [found], version_links=links)[0] == "unverified"


def test_proven_version_relationship_does_not_silently_accept_wrong_authors():
    parsed, found, link = _version_case()
    wrong_authors = candidate(metadata(year=2021, authors=["Another Author"], doi=found.metadata.doi))
    links = {wrong_authors.candidate_id: link}
    assert classify_reference(parsed, [wrong_authors], version_links=links)[0] == "unverified"


def test_preprint_version_suffixes_are_the_same_work():
    parsed, found, link = _version_case(parsed_arxiv="arXiv:2012.13255v2")
    assert classify_reference(parsed, [found], version_links={found.candidate_id: link})[0] == "matched"


def test_candidate_ids_bind_version_evidence_to_the_selected_candidate():
    parsed, found, link = _version_case()
    assert classify_reference(parsed, [found], version_links={"candidate_other": link})[0] == "unverified"


def test_no_candidates_preserves_unverified_outcome():
    assert classify_reference(metadata(), [])[:2] == ("unverified", None)


def test_a_valid_later_candidate_is_considered_after_a_wrong_same_title_candidate():
    parsed = metadata()
    found = [candidate(metadata(authors=["Different Author"])), candidate(candidate_id="candidate_2")]
    assert classify_reference(parsed, found)[:2] == ("matched", "candidate_2")


def test_dated_metadata_join_is_explicitly_reported_as_an_inference():
    parsed, found, link = _version_case()
    link = replace(link, relationship="dated-preprint-metadata", candidate_type="proceedings-article",
                   publication_year_range=(2020, 2022))
    outcome, _, reason = classify_reference(parsed, [found], version_links={found.candidate_id: link})
    assert outcome == "matched"
    assert "inference" in reason and "complete external author lists" in reason
    assert "identity link confirm" not in reason


@pytest.mark.parametrize("change", [
    {"candidate_type": "posted-content"}, {"candidate_type": "journal"}, {"candidate_type": None},
    {"publication_year_range": None}, {"publication_year_range": (2022, 2025)},
    {"cited_authors": ("Jane Doe",)}, {"cited_authors": ("Jane Doe", "Different Person")},
    {"cited_year": 2019}, {"cited_year": None}, {"cited_title": None},
    {"cited_title": "A precise title about language models and its companion"},
])
def test_dated_metadata_inference_rejects_insufficient_or_conflicting_source_metadata(change):
    parsed, found, link = _version_case()
    link = replace(link, relationship="dated-preprint-metadata", candidate_type="proceedings-article",
                   publication_year_range=(2020, 2022))
    links = {found.candidate_id: replace(link, **change)}
    assert classify_reference(parsed, [found], version_links=links)[0] == "unverified"


def test_dated_metadata_inference_cannot_accept_a_title_change_without_an_explicit_relationship():
    parsed, found, link = _version_case(changed_title=True)
    link = replace(link, relationship="dated-preprint-metadata", candidate_type="journal-article",
                   publication_year_range=(2020, 2022))
    assert classify_reference(parsed, [found], version_links={found.candidate_id: link})[0] == "unverified"


def test_dated_metadata_inference_does_not_accept_unrelated_matching_author_subset():
    parsed, found, link = _version_case()
    found = candidate(metadata(year=2021, doi=found.metadata.doi,
                               authors=["Jane Doe", "John Smith", "Different Person"]))
    link = replace(link, relationship="dated-preprint-metadata", candidate_type="journal-article",
                   publication_year_range=(2020, 2022))
    assert classify_reference(parsed, [found], version_links={found.candidate_id: link})[0] == "unverified"


def test_complete_external_author_comparison_accepts_ben_zaken_compound_surname_typography():
    parsed = metadata(year=2020, arxiv_id="2012.13255", authors=["Elad Ben Zaken", "Yoav Goldberg"])
    found = candidate(metadata(year=2021, doi="10.1234/publication", authors=parsed.authors))
    link = VersionLink(
        cited_arxiv_id="2012.13255", candidate_doi=found.metadata.doi, evidence_ref=_PROOF,
        relationship="dated-preprint-metadata", cited_title=parsed.title, cited_year=parsed.year,
        cited_authors=("Elad Ben-Zaken", "Yoav Goldberg"), candidate_type="proceedings-article",
        publication_year_range=(2020, 2022),
    )
    assert classify_reference(parsed, [found], version_links={found.candidate_id: link})[0] == "matched"
