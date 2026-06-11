#!/usr/bin/env bash
# shared-libraries 안전 커밋 — 지정 파일만 stage (models/대용량 제외)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

MSG="${1:-feat: shared-libraries update}"
shift || true

if [[ $# -gt 0 ]]; then
  git add "$@"
else
  git add \
    agents/ orchestrator/ llm/ ontology/ tests/ docs/ \
    harness/ notifications/ observability/ saas/ auth/ \
    CURSOR_HANDOVER.md requirements.txt pytest.ini
fi

git status --short
git commit -m "$MSG"
echo "✅ committed — push: git push origin main"
