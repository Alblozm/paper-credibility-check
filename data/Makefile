# Manifest-driven dataset pipeline
PY ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
PGDB ?= pcc_pilot
PSQL ?= psql

.PHONY: help pipeline resume pilot score sql validate db manifest assets

help:
	@echo "make pipeline  download/parse only; preserve released AI scores"
	@echo "make resume    resume download/parse only; no API key required"
	@echo "make pilot     rebuild the locked six-paper regression dataset without API calls"
	@echo "make sql       regenerate the complete PostgreSQL seed"
	@echo "make validate  run manifest and full cross-file acceptance gates"
	@echo "make db        load schema + complete manifest seed into PGDB=$(PGDB)"

pipeline:
	$(PY) scripts/run_pipeline.py --manifest data/manifests/pipeline_papers.json

resume:
	$(PY) scripts/run_pipeline.py --manifest data/manifests/pipeline_papers.json --resume

pilot:
	$(PY) scripts/run_six_paper_pipeline.py

manifest:
	$(PY) scripts/sync_parsed_provenance.py
	$(PY) scripts/build_manifest.py

score:
	@test -n "$$OPENAI_API_KEY" || (echo "OPENAI_API_KEY is required for genuine model scoring"; exit 2)
	$(PY) scripts/run_pipeline.py --manifest data/manifests/pipeline_papers.json --resume --score

sql:
	$(PY) scripts/export_all_structured_sql.py

validate:
	$(PY) scripts/validate_manifest.py
	$(PY) scripts/validate_full_dataset.py --manifest data/manifests/pipeline_papers.json --require-model-scoring --require-visual-qa

assets:
	$(PY) scripts/materialize_page_assets.py

db:
	$(PSQL) -v ON_ERROR_STOP=1 -d $(PGDB) -f sql/001_initial_schema.sql
	$(PSQL) -v ON_ERROR_STOP=1 -d $(PGDB) -f sql/020_seed_all_structured_papers.sql
	$(PSQL) -d $(PGDB) -c "SELECT count(*) AS papers FROM papers;"
