# shared-libraries — Cursor Agent 인수인계

> 최종 업데이트: 2026-06-11  
> **메타 HANDOVER**: `idea-collection/CURSOR_HANDOVER.md`

---

## 구현 완료 (2026-06-11)

| 모듈 | 상태 |
|------|------|
| `llm/client.py` | 5 Provider (local/openai/anthropic/google/azure) ✅ |
| `agents/orchestrator.py` | PIPELINE/CONSENSUS/DEBATE/FASTEST ✅ |
| `agents/planner.py` · `generator.py` · `reviewer.py` · `fixer.py` | LLM 프롬프트 + 파싱 ✅ |
| `orchestrator/workflow.py` | `AutoNoGaDaWorkflow` plan/generate/review/fix ✅ |
| `ontology/validator.py` | SemanticValidator · MED-SEM 20+ ✅ |
| `tests/test_workflow_e2e.py` | mock E2E (LM Studio 불필요) ✅ |

---

## 스크립트 (2026-06-11 정리)

| 스크립트 | 용도 |
|----------|------|
| `do_commit_sl.sh` | shared-libraries 안전 커밋 |
| `partner_four_agent_e2e.sh` | Partner API four_agent smoke |
| `test_lm_chat_wsl.py` | LM Studio WSL chat 스모크 |
| `probe_lm_studio.py` | URL 자동 감지 |

상세: `docs/SCRIPTS-REFERENCE.md`

---

## 다음 우선순위

1. **LM Studio 실연동** — `test_lm_chat_wsl.py` · `run_lm_studio_four_agent_tests.sh`
2. **Workflow IRB 초안** — `AutoNoGaDaWorkflow.run()` → ch45 §45.10
3. **harness** — 3-플랫폼 E2E 시나리오

---

## 테스트

```bash
cd projects/shared-libraries
python -m pytest tests/test_workflow_e2e.py -q
python scripts/test_lm_chat_wsl.py   # LM Studio 필요
```
