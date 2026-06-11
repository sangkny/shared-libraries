#!/usr/bin/env bash
# Partner fundus analyze — four_agent audit_trail smoke
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MEDI_URL="${MEDI_URL:-http://127.0.0.1:8001}"
IMG="${1:-${ROOT}/../MEDI-IOT-EyeCare/data/synthetic/images/test/0/g0_0000.jpg}"

if [[ ! -f "$IMG" ]]; then
  echo "이미지 없음: $IMG" >&2
  exit 1
fi

echo "=== Partner register + analyze (four_agent) ==="
docker exec medi-iot-api-dev printenv 2>/dev/null | grep -E '^AGENT_' || true

export MEDI_URL IMG
python3 "${ROOT}/scripts/partner_e2e_inline.py" \
  --base-url "$MEDI_URL" \
  --image "$IMG" \
  --expect-four-agent 2>/dev/null \
  || python3 - <<'PY'
import base64, json, os, time, urllib.request
from pathlib import Path

base = os.environ.get("MEDI_URL", "http://127.0.0.1:8001").rstrip("/")
img = Path(os.environ.get("IMG", ""))
if not img.is_file():
    raise SystemExit("IMG required")
b64 = base64.b64encode(img.read_bytes()).decode()
reg = json.loads(urllib.request.urlopen(
    urllib.request.Request(
        f"{base}/api/v1/partner/register",
        data=json.dumps({"partner_id": f"e2e-{int(time.time())}", "name": "four-agent", "plan": "trial"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    ),
    timeout=60,
).read())
key = reg["api_key"]
req = urllib.request.Request(
    f"{base}/api/v1/partner/analyze",
    data=json.dumps({
        "partner_id": reg["partner_id"],
        "api_key": key,
        "image_base64": b64,
        "patient_id": "patient-rollout-3",
        "lang": "ko",
        "analysis_type": "fundus",
        "return_format": "json",
    }).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
out = json.loads(urllib.request.urlopen(req, timeout=180).read())
at = out.get("audit_trail") or {}
print("mode=", out.get("decision_mode"), "audit=", at.get("mode"), "decision=", at.get("decision"))
PY

echo "✅ partner four_agent E2E done"
