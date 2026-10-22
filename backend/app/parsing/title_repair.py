"""Restore spacing and casing of a PDF title garbled by small capitals.

pypdf renders small-caps titles as "OPEN ESTIMATE : E VALUATING LLM S ...". The
model may only change spacing, punctuation spacing and letter case: its answer
is accepted only when its letters and digits equal the extracted title's.
"""
from __future__ import annotations

import json
import logging
import re

from app.parsing.base import ParsedDocument

logger = logging.getLogger(__name__)
PROMPT_VERSION = "title-repair-1.0"
_SPLIT_CAPS = re.compile(r"\b[A-Z] [A-Z]{2,}\b|\b[A-Z]{2,} [A-Z]\b")
_SYSTEM = "You restore the exact title of a paper from its first page. Return JSON only."


def _letters(text: str) -> str:
    return re.sub(r"[\W_]+", "", text).casefold()


def needs_repair(title: str | None) -> bool:
    if not title:
        return False
    letters = [c for c in title if c.isalpha()]
    mostly_upper = bool(letters) and sum(c.isupper() for c in letters) / len(letters) > 0.9
    return mostly_upper or bool(_SPLIT_CAPS.search(title))


def repair_title(doc: ParsedDocument, model_id: str) -> None:
    """Best effort: on any failure the extracted title is kept unchanged."""
    title = doc.paper.title
    if not needs_repair(title):
        return
    try:
        from app.pipeline.orchestrator import build_llm_client

        first_page = "\n".join(b.text for b in doc.blocks if b.page_start == 1)[:2000]
        task = {
            "task": (
                "The extracted title below has broken spacing or casing from small capitals. Return the title as "
                "the authors wrote it, with normal spacing and the authors' capitalization (keep acronyms such as "
                "LLMs). Do not add, remove or reorder words or characters."
            ),
            "extracted_title": title,
            "first_page_text": first_page,
            "output_schema": {"title": "string"},
        }
        response = build_llm_client(model_id).complete_json(
            _SYSTEM, json.dumps(task, ensure_ascii=False), temperature=0.0, max_tokens=4000,
        )
        repaired = " ".join(str(json.loads(response.text).get("title") or "").split())
    except Exception:  # noqa: BLE001 - cosmetic repair must never fail a review
        logger.warning("title repair failed; keeping the extracted title", exc_info=True)
        return
    if repaired and _letters(repaired) == _letters(title):
        doc.paper.title = repaired
    else:
        logger.info("title repair rejected: characters differ from the extracted title")
