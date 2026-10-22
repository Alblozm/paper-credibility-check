#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ -z "${PYTHON_BIN:-}" && -x .venv/bin/python ]]; then
  PYTHON_BIN=.venv/bin/python
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi
"$PYTHON_BIN" scripts/validate_manifest.py
"$PYTHON_BIN" scripts/validate_full_dataset.py \
  --manifest data/manifests/pipeline_papers.json \
  --require-model-scoring \
  --require-visual-qa

echo
echo "离线交付校验完成；没有重新评分，也不需要 API Key。"
echo "导入 PostgreSQL："
echo "  psql -v ON_ERROR_STOP=1 -d pcc_pilot -f sql/001_initial_schema.sql"
echo "  psql -v ON_ERROR_STOP=1 -d pcc_pilot -f sql/020_seed_all_structured_papers.sql"
