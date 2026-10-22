# PostgreSQL delivery

The current full backend import uses four files, in order:

1. `001_initial_schema.sql` — first-round schema.
2. `020_seed_all_structured_papers.sql` — 50 structured papers and five-dimension assessments.
3. `030_credibility_schema.sql` — independent round-two schema.
4. `032_credibility_seed_all_ai_batch.sql` — 50 three-dimension credibility reports.

```bash
psql -v ON_ERROR_STOP=1 -d pcc_pilot -f sql/001_initial_schema.sql
psql -v ON_ERROR_STOP=1 -d pcc_pilot -f sql/020_seed_all_structured_papers.sql
psql -v ON_ERROR_STOP=1 -d pcc_pilot -f sql/030_credibility_schema.sql
psql -v ON_ERROR_STOP=1 -d pcc_pilot -f sql/032_credibility_seed_all_ai_batch.sql
```

Files `002`, `010`, and `031` are single-paper demonstrations or historical compatibility seeds; they are not substitutes for the full delivery. Importing or rebuilding saved reports does not require an API key.
