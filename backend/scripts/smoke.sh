#!/usr/bin/env bash
# 端到端冒烟测试：建一个任务，轮询到出报告，检查关键字段。
# 用法：make smoke   或   ./scripts/smoke.sh [PORT]
set -euo pipefail

PORT="${1:-8000}"
BASE="http://localhost:${PORT}"

fail() { echo "✗ $1" >&2; exit 1; }

echo "→ ${BASE}/health"
curl -sf "${BASE}/health" > /tmp/pcc_health.json || fail "服务没起来，先 make run"
python3 -c "
import json
d = json.load(open('/tmp/pcc_health.json'))
print(f\"  schema {d['schema_version']} / rubric {d['rubric_version']} / llm {d['llm_provider']}\")
assert d['status'] == 'ok'
"

echo "→ POST /v1/assessments"
AID=$(curl -sf -X POST "${BASE}/v1/assessments" \
  -H 'content-type: application/json' \
  -d '{"source":{"type":"arxiv","arxiv_id":"2307.08122"},
       "venue_id":"iclr_2027_main","model_id":"model_fixture_v1"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['assessment_id'])") \
  || fail "创建任务失败"
echo "  assessment_id = ${AID}"

echo "→ 轮询 /v1/assessments/${AID}"
for i in $(seq 1 30); do
  curl -sf "${BASE}/v1/assessments/${AID}" > /tmp/pcc_snapshot.json || fail "查询失败"
  STATUS=$(python3 -c "import json;print(json.load(open('/tmp/pcc_snapshot.json'))['status'])")
  [ "$STATUS" = "completed" ] && break
  [ "$STATUS" = "failed" ] && break
  sleep 0.5
done

python3 - <<'PYEOF'
import json, sys
d = json.load(open("/tmp/pcc_snapshot.json"))
print(f"  status={d['status']} stage={d['stage']} progress={d['progress_percent']}%")

if d["status"] != "completed":
    print("✗ 任务没有完成：", json.dumps(d.get("error"), ensure_ascii=False))
    sys.exit(1)

r = d["report"]
o = r["overall_assessment"]
print(f"  overall={o['overall_score']} ({o['rating']}) provisional={o['is_provisional']}")
print(f"  dimensions={len(r['dimensions'])} checklist={len(r['reproducibility_checklist'])} evidence={len(r['evidence'])}")
print(f"  warnings={[w['code'] for w in d['warnings']]}")

# 契约里定死的硬规则，出问题必须在这里就拦住
assert d["stage"] == "report_ready", "completed 必须对应 report_ready"
assert d["progress_percent"] == 100, "completed 必须是 100%"
assert len(r["dimensions"]) == 5, "dimensions 恒为 5"
assert len(r["reproducibility_checklist"]) == 6, "checklist 恒为 6"
assert o["overall_score"] is None or 1.0 <= o["overall_score"] <= 10.0
evidence_ids = {e["evidence_id"] for e in r["evidence"]}
for dim in r["dimensions"]:
    for link in dim["evidence_links"]:
        assert link["evidence_id"] in evidence_ids, f"悬空证据 {link['evidence_id']}"
print("✓ 端到端通过")
PYEOF
