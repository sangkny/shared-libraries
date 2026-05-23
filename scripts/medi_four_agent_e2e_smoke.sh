#!/usr/bin/env bash
# MEDI fundus comprehensive — legacy vs four_agent smoke
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
IMG="${1:-${ROOT}/MEDI-IOT-EyeCare/data/synthetic/images/test/0/g0_0000.jpg}"
URL="${MEDI_URL:-http://127.0.0.1:8001/api/v1/lab/fundus/comprehensive}"

echo "=== ab_test (patient_id 분기) ==="
# patient-rollout-3 → MD5 bucket 5 (<10% four_agent)
for PID in patient-rollout-a patient-rollout-b patient-rollout-c patient-rollout-3; do
  curl -s -X POST "$URL" \
    -F "file=@${IMG}" -F "lang=ko" -F "patient_id=${PID}" \
    -F "lat=37.5665" -F "lng=126.9780" \
    -o "/tmp/medi_${PID}.json"
  python3 -c "
import json,sys
d=json.load(open('/tmp/medi_${PID}.json'))
at=d.get('audit_trail') or {}
print('${PID}: mode=', d.get('decision_mode'), 'ontology=', d.get('ontology_passed'),
      'audit_mode=', at.get('mode'), 'decision=', at.get('decision','-'))
"
done
docker exec medi-iot-api-dev printenv 2>/dev/null | grep -E '^AGENT_' || true
echo "참고: 10% 롤아웃이면 대부분 legacy — four_agent 확인은 patient-rollout-3"
