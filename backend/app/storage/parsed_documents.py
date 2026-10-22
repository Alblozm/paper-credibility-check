"""Persistence boundary for generic structured papers."""
from __future__ import annotations

from app.parsing.base import ParsedDocument
from app.storage.db import SessionLocal, init_db
from app.storage.models import PaperRow, ParsedDocRow


def parsed_document_payload(doc: ParsedDocument) -> dict:
    """Serialize the shared ParsedDocument contract into JSON-safe database content."""
    return {
        "paper": doc.paper.model_dump(mode="json"),
        "blocks": [
            {
                "text": block.text,
                "page_start": block.page_start,
                "page_end": block.page_end,
                "chunk_id": block.chunk_id,
                "section_id": block.section_id,
                "section_title": block.section_title,
                "content_type": block.content_type,
                "location_confidence": block.location_confidence.value,
            }
            for block in doc.blocks
        ],
        "elements": [
            {
                "element_id": element.element_id,
                "element_type": element.element_type,
                "label": element.label,
                "caption": element.caption,
                "page_number": element.page_number,
                "section_id": element.section_id,
                "extracted_text": element.extracted_text,
                "parse_quality": element.parse_quality.value,
            }
            for element in doc.elements
        ],
        "links": [
            {
                "url": link.url,
                "link_type": link.link_type,
                "source_chunk_id": link.source_chunk_id,
            }
            for link in doc.links
        ],
        "warnings": doc.warnings,
        "source_sha256": doc.source_sha256,
        "parser_name": doc.parser_name,
        "source_categories": doc.source_categories,
    }


def store_parsed_document(doc: ParsedDocument) -> int:
    """Upsert paper identity and insert an immutable parse snapshot."""
    init_db()
    with SessionLocal.begin() as session:
        row = session.get(PaperRow, doc.paper.paper_id)
        if row is None:
            row = PaperRow(id=doc.paper.paper_id)
            session.add(row)
        row.sha256 = doc.source_sha256
        row.arxiv_id = doc.paper.arxiv_id
        row.title = doc.paper.title
        row.storage_uri = doc.source_path
        parsed = ParsedDocRow(
            paper_id=doc.paper.paper_id,
            parser=doc.parser_name,
            parse_quality=doc.parse_quality.value,
            content=parsed_document_payload(doc),
        )
        session.add(parsed)
        session.flush()
        return parsed.id
