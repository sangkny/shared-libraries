#!/usr/bin/env bash
# 4-에이전트 gradual_rollout — compose 재기동 (ab_test 10%)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ENV_FILE="${ROOT}/.env.local"

echo "=== 4-에이전트 gradual_rollout 적용 ==="
if [[ -f "$ENV_FILE" ]]; then
  grep -E '^AGENT_' "$ENV_FILE" || true
else
  echo "WARN: $ENV_FILE 없음 — compose 기본값(legacy) 사용"
fi

cd "$ROOT"
docker compose -f docker-compose.dev.yml up -d --force-recreate \
  medi-iot-api coops-api autonogada-api shared-libs 2>&1 | tail -8

echo "✅ 재기동 완료 — MEDI :8001 · CoOps :8003 · ADK :8002"
echo "검증: bash shared-libraries/scripts/medi_four_agent_e2e_smoke.sh"
