# shared-libraries — Cursor Agent 인수인계

> 최종 업데이트: 2026-06-11  
> **메타 HANDOVER**: `idea-collection/CURSOR_HANDOVER.md`

---

## LM Studio 네트워크 (2026-06-11) ✅

| 환경 | URL |
|------|-----|
| WSL | `http://192.168.0.12:1234/v1` |
| Docker | `http://host.docker.internal:1234/v1` |
| Windows | `http://localhost:1234/v1` |

- **문제**: SVG-Stock `:8000` 점유 → LM Studio 8000 충돌
- **해결**: LM Studio **1234** + Serve on Local Network
- **검증**: WSL·컨테이너 4모델 · `test_lm_chat_wsl.py` ✅
- **SSOT**: `../docs/NETWORK-GUIDE.md` · `.env.example`

```bash
cp .env.example .env.local
python scripts/probe_lm_studio.py
python scripts/test_lm_chat_wsl.py
```

---

## 구현 완료 (2026-06-11)

| 모듈 | 상태 |
|------|------|
| `llm/client.py` | 5 Provider ✅ |
| `agents/*` | Planner/Generator/Reviewer/Fixer ✅ |
| `orchestrator/workflow.py` | AutoNoGaDaWorkflow ✅ |
| `tests/test_workflow_e2e.py` | mock E2E ✅ |
| `scripts/probe_lm_studio.py` | 1234 우선 탐색 ✅ |

---

## 다음 우선순위

1. **Workflow IRB** — `AutoNoGaDaWorkflow.run()` → ch45 §45.10
2. **harness** — 3-플랫폼 E2E
3. **run_lm_studio_four_agent_tests.sh** — 실 LLM 통합

---

## 테스트

```bash
python -m pytest tests/test_workflow_e2e.py -q
python scripts/test_lm_chat_wsl.py
bash scripts/run_lm_studio_four_agent_tests.sh
```
