#!/usr/bin/env bash
# LM Studio 실연동 — Docker shared-libs 컨테이너에서 실행 (권장)
# Windows LM Studio: http://127.0.0.1:8000  →  host.docker.internal:8000
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
docker compose -f docker-compose.dev.yml up -d shared-libs
docker exec \
  -e LOCAL_BASE_URL=http://host.docker.internal:8000/v1 \
  -e LM_STUDIO_BASE_URL=http://host.docker.internal:8000/v1 \
  -e LM_STUDIO_AVAILABLE=1 \
  -e AGENT_DECISION_MODE=four_agent \
  -e AGENT_FOUR_AGENT_MOCK= \
  shared-libs-dev \
  python -m pytest /app/tests/integration/test_four_agent_real_llm.py \
    /app/tests/integration/test_orchestrator_four_agent.py \
    -v -k "lm_studio or real_llm" --lm-studio-required --tb=short "$@"
