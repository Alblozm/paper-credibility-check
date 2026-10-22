"""Frozen bibliography fragments and counterexamples for boundary fixes; offline."""

from __future__ import annotations

import pytest

from app.credibility.reference_extraction import extract_reference_entries, metadata_hints
from app.parsing.base import TextBlock


def block(text: str, chunk_id: str = "refs", page: int = 15) -> TextBlock:
    return TextBlock(
        text=text, chunk_id=chunk_id, page_start=page, page_end=page,
        section_role="references", section_title="References",
    )


def test_full_wrapped_author_list_starts_after_a_reference_without_a_year():
    # Original LoRA bibliography fragments: the first pair is adjacent and its
    # first citation has no year; later fragments exercise wrapped author lists.
    raw = (
        "Alec Radford, Jeffrey Wu, Rewon Child, David Luan, Dario Amodei, and Ilya Sutskever. Language\n"
        "Models are Unsupervised Multitask Learners. pp. 24, b.\n"
        "Pranav Rajpurkar, Robin Jia, and Percy Liang. Know what you don’t know: Unanswerable questions\n"
        "for squad. CoRR, abs/1806.03822, 2018. URL http://arxiv.org/abs/1806.03822.\n"
        "Andreas R ¨uckl´e, Gregor Geigle, Max Glockner, Tilman Beck, Jonas Pfeiffer, Nils Reimers, and\n"
        "Iryna Gurevych. Adapterdrop: On the efﬁciency of adapters in transformers, 2020.\n"
        "Tara N Sainath, Brian Kingsbury, Vikas Sindhwani, Ebru Arisoy, and Bhuvana Ramabhadran. Low-\n"
        "rank matrix factorization for deep neural network training with high-dimensional output targets.\n"
        "In 2013 IEEE international conference on acoustics, speech and signal processing , pp. 6655–\n"
        "6659. IEEE, 2013."
    )
    entries, total, gaps = extract_reference_entries([block(raw)])
    assert total == len(entries) == 4 and not gaps
    assert [metadata_hints(e.text)["authors"][0] for e in entries] == [
        "Alec Radford", "Pranav Rajpurkar", "Andreas R ¨uckl´e", "Tara N Sainath",
    ]
    assert metadata_hints(entries[0].text)["year"] is None
    assert "6659. IEEE, 2013." in entries[-1].text
    assert all(e.fragments[0].text in raw for e in entries)


def test_long_author_list_does_not_start_more_entries_inside_it():
    raw = (
        "Jane Doe. A previous method. A Journal, 2020.\n"
        "Tom B. Brown, Benjamin Mann, Nick Ryder, Melanie Subbiah, Jared Kaplan, Prafulla Dhari-\n"
        "wal, Arvind Neelakantan, Pranav Shyam, Girish Sastry, Amanda Askell, Sandhini Agarwal,\n"
        "Ilya Sutskever, and Dario Amodei. Language Models are Few-Shot Learners. arXiv:2005.14165\n"
        "[cs], July 2020. URL http://arxiv.org/abs/2005.14165.\n"
        "John Roe. A following method. Another Journal, 2021."
    )
    entries, total, gaps = extract_reference_entries([block(raw)])
    assert total == len(entries) == 3 and not gaps
    hints = metadata_hints(entries[1].fragments[0].text)
    assert hints["authors"][0] == "Tom B. Brown"
    assert hints["authors"][5] == "Prafulla Dhariwal"
    assert hints["authors"][-1] == "Dario Amodei"
    assert hints["title"] == "Language Models are Few-Shot Learners"


@pytest.mark.parametrize("continuation", [
    "Linguistics. doi: 10.18653/v1/2020.ﬁndings-emnlp.41. URL https://aclanthology.org/x.",
    "Processing, pp. 1631–1642, Seattle, Washington, USA, October 2013. Association for Computa-\n"
    "tional Linguistics. URL https://aclanthology.org/D13-1170.",
    "Natural Language Processing: System Demonstrations , pp. 38–45, Online, October 2020. As-\n"
    "sociation for Computational Linguistics. URL https://www.aclweb.org/anthology/\n2020.emnlp-demos.6.",
])
def test_publication_and_url_tail_lines_do_not_become_references(continuation):
    raw = (
        "Jane Doe and John Roe. A useful method. In a Conference, 2020.\n"
        + continuation + "\nJohn Smith. A different method. In a Journal, 2021."
    )
    entries, total, gaps = extract_reference_entries([block(raw)])
    assert total == len(entries) == 2 and not gaps
    assert continuation in entries[0].fragments[0].text
    assert entries[1].text.startswith("John Smith.")


@pytest.mark.parametrize(("raw", "authors", "title"), [
    (
        "William B. Dolan and Chris Brockett. Automatically constructing a corpus of sentential paraphrases. 2005.",
        ["William B. Dolan", "Chris Brockett"],
        "Automatically constructing a corpus of sentential paraphrases",
    ),
    (
        "Diederik P. Kingma and Jimmy Ba. Adam: A method for stochastic optimization, 2017.",
        ["Diederik P. Kingma", "Jimmy Ba"], "Adam: A method for stochastic optimization",
    ),
    ("Doe, J. Deep Learning. 2024.", ["Doe, J"], "Deep Learning"),
    ("Doe, J., Roe, R. Deep Learning. 2024.", ["Doe, J", "Roe, R"], "Deep Learning"),
    ("J. R. Doe. Deep Learning. 2024.", ["J. R. Doe"], "Deep Learning"),
])
def test_initials_preserve_the_full_author_and_do_not_swallow_title(raw, authors, title):
    hints = metadata_hints(raw)
    assert hints["authors"] == authors
    assert hints["title"] == title


def test_only_the_actual_block_page_footer_is_removed():
    first = block("References\nJane Doe. A method. A Journal, 2024.\n13", "a", 13)
    second = block("John Roe. Another method. A Journal, 2023.\n15", "b", 14)
    entries, total, gaps = extract_reference_entries([first, second])
    assert total == len(entries) == 2 and not gaps
    assert entries[0].fragments[0].text == "Jane Doe. A method. A Journal, 2024."
    # An arbitrary final numeric line is retained, since it is not page 14.
    assert entries[1].fragments[0].text.endswith("\n15")
    assert entries[0].fragments[0].text in first.text
    assert entries[1].fragments[0].text in second.text


def test_wrapped_identifiers_do_not_supply_an_identifier_year():
    raw = (
        "Daniel Cer, Mona Diab, and Lucia Specia. Semantic evaluation. A Conference, 2017. "
        "doi: 10.18653/\nv1/s17-2001. arXiv:1811.\n03962."
    )
    hints = metadata_hints(raw)
    assert hints["year"] == 2017
    assert hints["doi"] == "10.18653/v1/s17-2001"
    assert hints["arxiv_id"] == "1811.03962"


def test_query_only_word_joining_keeps_inline_hyphens_and_original_fragments():
    raw = (
        "Jane Doe. An over-\nparameterized low-rank method. A Conference, 2024.\n"
        "John Roe. Another method. A Journal, 2023."
    )
    entries, total, gaps = extract_reference_entries([block(raw)])
    assert total == len(entries) == 2 and not gaps
    assert "over-\nparameterized" in entries[0].fragments[0].text
    assert metadata_hints(entries[0].fragments[0].text)["title"] == "An overparameterized low-rank method"
    assert "over- parameterized" in entries[0].text


def test_query_word_joining_never_crosses_a_blank_paragraph():
    hints = metadata_hints("Jane Doe. A low-\n\nrank method. A Journal, 2024.")
    assert hints["title"] == "A low- rank method"


def test_et_al_is_a_truncation_marker_rather_than_an_author_name():
    hints = metadata_hints("Jane Doe, John Roe, et al. A useful method. 2024.")
    assert hints["authors"] == ["Jane Doe", "John Roe"]
    assert hints["title"] == "A useful method"


def test_publication_running_header_at_next_page_cannot_swallow_its_references():
    first = block(
        "REFERENCES\nWilliam Merrill, Ashish Sabharwal, and Noah A. Smith. Satur ated transformers are "
        "constant-depth\nthreshold circuits. TACL, 10:843–856, 2022.\n10", "refs_10", 10,
    )
    second = block(
        "Published as a conference paper at ICLR 2024\n"
        "Maxwell Nye, Anders Andreassen, Guy Gur-Ari, Henryk Michal ewski, Jacob Austin, David Bieber,\n"
        "David Dohan, Aitor Lewkowycz, Maarten Bosma, David Luan, Ch arles Sutton, and Augustus\n"
        "Odena. Show your work: Scratchpads for intermediate comput ation with language models. arXiv,\n"
        "abs/2112.00114, 2021.\n"
        "Jorge P´ erez, Pablo Barcel´ o, and Javier Marinkovic. Atten tion is Turing complete. JMLR, 22(1),\n"
        "January 2021.", "refs_11", 11,
    )
    entries, total, gaps = extract_reference_entries([first, second])
    assert len(entries) == total == 3 and not gaps
    assert entries[0].text.endswith("2022.")
    assert entries[1].text.startswith("Maxwell Nye,")
    assert entries[2].text.startswith("Jorge P´ erez,")
    assert all("Published as" not in e.text for e in entries)
    assert entries[1].fragments[0].text in second.text
    assert entries[1].fragments[0].chunk_id == "refs_11"


@pytest.mark.parametrize(("authors", "title"), [
    (
        "Guhao Feng, Bohang Zhang, Y untian Gu, Haotian Y e, Di He, and L iwei Wang",
        "Towards revealing the mystery behind chain of thought: A theoretical perspect ive",
    ),
    (
        "William Merrill, Vivek Ramanujan, Y oav Goldberg, Roy Schwa rtz, and Noah A. Smith",
        "Effects of parameter norm growth during transformer training",
    ),
    (
        "Jason Wei, Xuezhi Wang, Dale Schuurmans, Maarten Bosma, bri an ichter, Fei Xia, Ed H. Chi,\n"
        "Quoc V Le, and Denny Zhou",
        "Chain of thought prompting elicits reasoning in large language models",
    ),
])
def test_character_positioned_name_fragments_do_not_shift_an_author_boundary(authors, title):
    raw = f"Jane Doe. First citation. 2024.\n{authors}. {title}. In NeurIPS, 2023.\nJohn Roe. Last citation. 2024."
    entries, total, gaps = extract_reference_entries([block(raw)])
    assert len(entries) == total == 3 and not gaps
    assert entries[1].text.startswith(authors.split("\n")[0])
    hints = metadata_hints(entries[1].text)
    assert hints["authors"][0] == authors.split(",")[0]
    assert hints["authors"][-1] == authors.rsplit(" and ", 1)[-1]
    assert hints["title"] == title
    assert entries[1].fragments[0].text in raw


def test_unrecognised_page_prefix_cannot_silently_certify_a_whole_merged_page():
    first = block("Jane Doe. A method. A Journal, 2024.\n10", "refs_10", 10)
    second = block("Unrecognised running page header\nJohn Roe. Another method. 2023.", "refs_11", 11)
    _, total, gaps = extract_reference_entries([first, second])
    assert total is None
    assert any("unresolved author boundary" in g for g in gaps)
