"""PDF quantity spacing has no effect on value/unit, without changing source text."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.credibility.claim_model import _Extraction, _number_tokens, _validate_extraction
from app.credibility.context import CheckContext
from app.parsing.base import ParsedDocument, TextBlock
from app.schemas.paper import Paper


@pytest.mark.parametrize(("compact", "spaced"), [
    ("3.7%", "3 . 7%"), ("13.5%", "13 . 5%"), ("9.1%", "9. 1%"),
    ("5.5km", "5.5 km"), ("5.5km", "5 . 5 km"), ("-3.7%", "- 3 . 7 %"),
    ("175B", "175 B"), ("10GB", "10 GB"), ("5.5ms", "5.5 ms"),
    ("1.2GHz", "1.2 GHz"), ("850hPa", "850 hPa"),
])
def test_scalar_decimal_and_known_unit_spacing_preserve_quantity_identity(compact, spaced):
    left, right = _number_tokens(compact), _number_tokens(spaced)
    assert len(left) == len(right) == 1
    assert left[0].key == right[0].key
    assert right[0].raw == spaced


def _extracted_context(tmp_path, statement_quantity, quote_quantity):
    quote = f"The reported quantity is {quote_quantity}."
    ctx = CheckContext(source={}, repo_root=tmp_path, generated_at="now", ref=None, unresolved_reason=None,
                       doc=ParsedDocument(paper=Paper(paper_id="quantity", title="Quantity test", parse_quality="good"),
                                          blocks=[TextBlock(quote, 1, chunk_id="intro", section_role="introduction")]))
    result = _Extraction.model_validate({
        "selection_scope": "The full introduction.", "scope_complete": True, "scope_limitations": [], "claims": [{
            "claim_id": "c", "statement": f"The reported quantity is {statement_quantity}.",
            "importance": "core", "claim_conditions": {}, "claim_evidence": [{
                "evidence_id": "e", "mode": "quote", "excerpt": quote, "chunk_id": "intro", "values": [],
            }],
        }],
    })
    return ctx, result, quote


@pytest.mark.parametrize(("statement_quantity", "source_quantity"), [
    ("3.7%", "3 . 7%"), ("9.3%", "9 . 3%"), ("13.5%", "13 . 5%"),
    ("5.5 km", "5.5km"), ("5.5km", "5.5 km"),
])
def test_extraction_numeric_guard_accepts_same_quantity_and_preserves_actual_quote(
    tmp_path, statement_quantity, source_quantity,
):
    ctx, result, quote = _extracted_context(tmp_path, statement_quantity, source_quantity)
    _validate_extraction(ctx, result)
    span = result.claims[0].claim_evidence[0]
    assert span.excerpt == quote
    assert span.values == [source_quantity]


@pytest.mark.parametrize(("statement_quantity", "source_quantity"), [
    ("3.7%", "3.7"), ("3.7%", "- 3 . 7%"), ("3.7%", "3 . 9%"),
    ("5.5km", "5.5 m"), ("5.5km", "5.5 cm"), ("5.5ms", "5.5 s"),
    ("7B", "7 MB"), ("10x", "10"), ("10%", "10 x"),
    ("3.7", "3 · 7"), ("3.7", "3 * 7"), ("3.7", "3 + 7"),
    ("3.7", "3 - 7"), ("3.7", "3–7"), ("3.7", "3 .. 7"),
    ("10000", "10⁴"), ("5.5km", "5.5 k m"),
])
def test_numeric_guard_still_rejects_changed_units_values_signs_ranges_and_operators(
    tmp_path, statement_quantity, source_quantity,
):
    ctx, result, _ = _extracted_context(tmp_path, statement_quantity, source_quantity)
    with pytest.raises(ValueError, match="statement number"):
        _validate_extraction(ctx, result)


def test_plain_following_words_are_not_swallowed_as_units():
    tokens = _number_tokens("There were 3 results and 7 models.")
    assert [token.key for token in tokens] == [(Decimal(3), ""), (Decimal(7), "")]


@pytest.mark.parametrize("connector", ["of", "Of", "OF"])
def test_pdf_joined_of_quantity_has_the_same_identity_as_spaced_source(tmp_path, connector):
    ctx, result, quote = _extracted_context(tmp_path, "13.2", f"average training speedup {connector}13.2 times")
    _validate_extraction(ctx, result)
    span = result.claims[0].claim_evidence[0]
    assert span.excerpt == quote
    assert span.values == ["13.2"]


@pytest.mark.parametrize("joined", ["Model13.2", "proof13.2", "modelof13.2", "GPT13.2", "Llama13B"])
def test_arbitrary_name_digits_are_not_reinterpreted_as_quantities(joined):
    assert _number_tokens(joined) == []


@pytest.mark.parametrize("quantity", ["-13.2", "13.2%", "13.2x"])
def test_pdf_joined_of_quantity_does_not_drop_signs_or_units(tmp_path, quantity):
    ctx, result, _ = _extracted_context(tmp_path, "13.2", f"speedup of{quantity}")
    with pytest.raises(ValueError, match="statement number"):
        _validate_extraction(ctx, result)
