"""Model labels for the URLs a paper prints: the authors' own code or project page, or someone else's.

A fixed "our code is available at" pattern misses bare footnote links and project
pages printed under the author list. The model only labels each printed URL with
its surrounding text; fetching, README/page matching and association stay in
app.credibility.code_checks.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.credibility.context import CheckContext
from app.credibility.model_labels import label_items, label_or_none
from app.credibility.models import CredibilityModelCall
from app.schemas.common import StrictModel

PROMPT_VERSION = "credibility-paper-links-1.0"
PURPOSE = "paper_link_classification"
MAX_LINKS = 40
Role = Literal["own_code", "own_project_page", "own_data_or_model", "third_party", "unclear"]
OWN_ROLES = {"own_code", "own_project_page"}

_SYSTEM = (
    "You label URLs printed in a machine-learning paper by their relationship to THIS paper. "
    "Use only the provided paper identity and surrounding text. Return JSON only."
)


class LinkLabel(StrictModel):
    link_id: str
    role: Role
    rationale: str = Field(max_length=300)


class _Labels(StrictModel):
    links: list[LinkLabel]


def classify_paper_links(ctx: CheckContext, links: list[dict], calls: list[CredibilityModelCall]) -> dict | None:
    """Return {link_id: role}, {} when there is nothing to label, or None to keep the sentence rules."""
    if not links:
        return {}
    paper = ctx.doc.paper if ctx.doc is not None else None
    task = {
        "task": (
            "For each link, decide its role: 'own_code' if the paper presents it as the authors' code, "
            "implementation, package or repository for this work (including a bare footnote or a link printed "
            "next to this paper's own method/benchmark name); 'own_project_page' if it is this paper's project or "
            "homepage (for example a site printed under the title and author list); 'own_data_or_model' if it only "
            "hosts this paper's data, weights or demo; 'third_party' if it belongs to other work such as baselines, "
            "tools, datasets or cited papers; 'unclear' otherwise. Return exactly the given link_ids."
        ),
        "paper": {
            "title": paper.title if paper else None,
            "authors": (paper.authors[:12] if paper else []),
        },
        "output_schema": {"links": [{
            "link_id": "string",
            "role": "own_code | own_project_page | own_data_or_model | third_party | unclear",
            "rationale": "string",
        }]},
        "links": links,
    }
    labels = label_or_none(lambda: label_items(
        ctx, purpose=PURPOSE, prompt_version=PROMPT_VERSION, system=_SYSTEM, task=task, schema=_Labels,
        expected_ids=[row["link_id"] for row in links], calls=calls, id_field="link_id",
    ), PURPOSE)
    return None if labels is None else {row.link_id: row.role for row in labels.links}
