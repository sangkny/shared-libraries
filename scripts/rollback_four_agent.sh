#!/usr/bin/env bash
# 4-에이전트 롤백 — before-four-agent-v1.0 태그 기준 파일 복원 + legacy 모드
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECTS="$(cd "$ROOT/../.." && pwd)/projects"
ENV_FILE="${PROJECTS}/.env.local"

echo "=== 4-에이전트 롤백 ==="
cd "$ROOT"

git checkout before-four-agent-v1.0 -- \
  agents/reviewer.py \
  ontology/validator.py \
  agents/pipeline.py 2>/dev/null || true

# 신규 파일은 legacy 모드로 무력화 (체크아웃 대상에 없던 파일)
for f in agents/feature_flags.py agents/decision_gate.py agents/four_agent_types.py; do
  if [[ -f "$f" ]]; then
    : # 유지 — feature_flags 는 legacy 기본값으로 동작
  fi
done

if [[ -f "$ENV_FILE" ]]; then
  if sed --version 2>/dev/null | grep -q GNU; then
    sed -i 's/^AGENT_DECISION_MODE=.*/AGENT_DECISION_MODE=legacy/' "$ENV_FILE"
  else
    sed -i '' 's/^AGENT_DECISION_MODE=.*/AGENT_DECISION_MODE=legacy/' "$ENV_FILE" 2>/dev/null \
      || sed -i 's/^AGENT_DECISION_MODE=.*/AGENT_DECISION_MODE=legacy/' "$ENV_FILE"
  fi
  grep -q '^AGENT_DECISION_MODE=' "$ENV_FILE" \
    || echo 'AGENT_DECISION_MODE=legacy' >> "$ENV_FILE"
fi

if [[ -f "${PROJECTS}/docker-compose.dev.yml" ]]; then
  docker compose -f "${PROJECTS}/docker-compose.dev.yml" \
    restart medi-iot-api coops-api 2>/dev/null || true
fi

echo "✅ 롤백 완료 (AGENT_DECISION_MODE=legacy)"
