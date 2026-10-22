# Scripts

Planned utilities, added incrementally after the three pilot papers are fixed:

- `fetch_arxiv.py`: fetch and checksum the exact arXiv version
- `fetch_openreview.py`: preserve public review metadata and provenance
- `validate_manifest.py`: validate identifiers, enums and required sources
- `validate_annotations.py`: enforce Field Contract combinations and evidence links
- `export_for_backend.py`: produce a stable backend-ingestion package

Scripts must cache source responses, identify requests responsibly and never
commit credentials or user-uploaded documents.
