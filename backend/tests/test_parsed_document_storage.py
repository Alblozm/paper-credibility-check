from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.parsing.grobid import StubParser
from app.storage import parsed_documents
from app.storage.models import Base, PaperRow, ParsedDocRow


def test_structured_document_is_persisted_as_json_snapshot(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'parsed.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(parsed_documents, "SessionLocal", sessions)
    monkeypatch.setattr(parsed_documents, "init_db", lambda: None)

    doc = StubParser().parse_arxiv("2501.01234")
    doc.source_sha256 = "a" * 64
    doc.source_path = str(tmp_path / "private.pdf")
    doc.parser_name = "test-parser-v1"
    row_id = parsed_documents.store_parsed_document(doc)

    with sessions() as session:
        paper = session.get(PaperRow, doc.paper.paper_id)
        parsed = session.scalar(select(ParsedDocRow).where(ParsedDocRow.id == row_id))
        assert paper is not None and paper.storage_uri == doc.source_path
        assert parsed is not None and parsed.parser == "test-parser-v1"
        assert parsed.content["blocks"][0]["chunk_id"]
        assert parsed.content["source_sha256"] == "a" * 64
