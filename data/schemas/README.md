# Schemas

Machine-readable JSON Schemas for the 0.6.0 AI-authoritative release contract.

Available schemas:

- `paper.schema.json`
- `parsed_paper.schema.json`
- `annotation.schema.json`
- `evidence.schema.json`
- `benchmark_manifest.schema.json`

The JSON Schemas validate interchange files; PostgreSQL constraints in
`sql/001_initial_schema.sql` validate the relational representation.
