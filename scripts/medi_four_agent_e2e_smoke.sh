#!/usr/bin/env bash
# MEDI fundus comprehensive — legacy vs four_agent smoke
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
IMG="${1:-${ROOT}/MEDI-IOT-EyeCare/data/synthetic/images/test/0/g0_0000.jpg}"
URL="${MEDI_URL:-http://127.0.0.1:8001/api/v1/lab/fundus/comprehensive}"

echo "=== Legacy (container env default) ==="
curl -s -X POST "$URL" \
  -F "file=@${IMG}" -F "lang=ko" -F "lat=37.5665" -F "lng=126.9780" \
  -o /tmp/medi_legacy.json
python3 <<'PY'
import json
d = json.load(open("/tmp/medi_legacy.json"))
print("decision_mode:", d.get("decision_mode", "N/A"))
print("ontology_passed:", d.get("ontology_passed"))
print("dr_grade:", d.get("dr_grade"))
print("audit_trail:", "audit_trail" in d)
PY

echo "Note: four_agent requires AGENT_DECISION_MODE=four_agent on medi-iot-api container"
