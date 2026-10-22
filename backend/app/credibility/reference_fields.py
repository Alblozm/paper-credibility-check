"""Model splitting of raw reference strings into title, authors, year and identifiers.

Sentence rules cut "What are the odds? language models are capable ..." at the
question mark and keep "accuracy.ACM Transactions ..." as one title when PDF
extraction drops a space. The model splits each raw entry; a field is used only
when its letters and digits appear in that entry, so nothing is invented.
"""
from __future__ import annotations

import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor

from pydantic import Field

from app.credibility.context import CheckContext
from app.credibility.model_labels import label_items, label_or_none
from app.credibility.models import CredibilityModelCall
from app.schemas.common import StrictModel

PROMPT_VERSION = "credibility-reference-fields-1.0"
PURPOSE = "reference_field_parsing"
BATCH_SIZE = 40
_SYSTEM = (
    "You split bibliography entries from a paper into fields. Copy text exactly from each entry; "
    "never complete, correct or look up missing information. Return JSON only."
)


class ReferenceFields(StrictModel):
    entry_id: str
    title: str | None = Field(default=None, max_length=500)
    authors: list[str] = Field(default_factory=list, max_length=100)
    year: int | None = None
    doi: str | None = None
    arxiv_id: str | None = None


class _Fields(StrictModel):
    entries: list[ReferenceFields]


def _alnum(text: str) -> str:
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", text)).casefold()


def parse_reference_fields(
    ctx: CheckContext, raws: list[str], calls: list[CredibilityModelCall],
) -> list[dict | None] | None:
    """Model fields per raw entry (None where unusable), or None to keep the sentence rules for all."""
    if not raws:
        return []
    batches = [list(range(start, min(start + BATCH_SIZE, len(raws)))) for start in range(0, len(raws), BATCH_SIZE)]

    def run(indices: list[int]):
        batch_calls: list[CredibilityModelCall] = []
        task = {
            "task": (
                "For each entry return: title (the cited work's title only, without venue, volume, pages or "
                "URL; keep its question marks and colons), authors (as printed, one string per person; "
                "an organisation author is one string), year (publication year), doi and arxiv_id (only if "
                "printed). Use null or [] when a field is not printed. Return exactly the given entry_ids."
            ),
            "output_schema": {"entries": [{
                "entry_id": "string", "title": "string | null", "authors": ["string"], "year": "integer | null",
                "doi": "string | null", "arxiv_id": "string | null",
            }]},
            "entries": [{"entry_id": f"E{index + 1}", "text": raws[index]} for index in indices],
        }
        labels = label_items(
            ctx, purpose=PURPOSE, prompt_version=PROMPT_VERSION, system=_SYSTEM, task=task, schema=_Fields,
            expected_ids=[f"E{index + 1}" for index in indices], calls=batch_calls, id_field="entry_id",
            max_tokens=12000,
        )
        return batch_calls, labels

    with ThreadPoolExecutor(max_workers=min(3, len(batches))) as pool:
        results = [label_or_none(lambda fn=future: fn.result(), PURPOSE)
                   for future in [pool.submit(run, batch) for batch in batches]]
    if all(result is None for result in results):
        return None
    fields: list[dict | None] = [None] * len(raws)
    for result in results:
        if result is None:
            continue
        batch_calls, labels = result
        calls.extend(batch_calls)
        for row in labels.entries:
            index = int(row.entry_id[1:]) - 1
            fields[index] = _grounded(row, raws[index])
    return fields


def _grounded(row: ReferenceFields, raw: str) -> dict:
    """Keep only fields whose characters are printed in this entry."""
    text = _alnum(raw)
    title = row.title if row.title and len(_alnum(row.title)) >= 6 and _alnum(row.title) in text else None
    authors = row.authors if row.authors and all(
        _alnum(name.split()[-1]) in text for name in row.authors if name.split()
    ) else []
    return {
        "title": title,
        "authors": [name.strip() for name in authors if name.strip()],
        "year": row.year if row.year and str(row.year) in raw else None,
        "doi": row.doi.casefold() if row.doi and _alnum(row.doi) in text else None,
        "arxiv_id": row.arxiv_id if row.arxiv_id and _alnum(row.arxiv_id) in text else None,
    }


def merge_hints(rule_hints: dict, model_fields: dict | None) -> dict:
    """Model title/authors/year fill the rule hints; printed identifiers keep the stricter rules first."""
    if not model_fields:
        return rule_hints
    merged = dict(rule_hints)
    for key in ("title", "authors", "year"):
        if model_fields.get(key):
            merged[key] = model_fields[key]
    for key in ("doi", "arxiv_id"):
        if not merged.get(key) and model_fields.get(key):
            merged[key] = model_fields[key]
    return merged
