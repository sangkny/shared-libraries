# shared-libraries — Cursor Agent 인수인계

> 최종 업데이트: 2026-06-11 · Git: `3f938b1`  
> **메타 HANDOVER**: `idea-collection/CURSOR_HANDOVER.md`

---

## LM Studio 실연동 ✅

| 환경 | URL |
|------|-----|
| WSL | `http://192.168.0.12:1234/v1` |
| Docker | `http://host.docker.internal:1234/v1` |

**모델**: gemma-4-26b · gemma-4-e4b · mistral-7b · nomic-embed

| 검증 | 결과 |
|------|------|
| `test_lm_chat_wsl.py` | ✅ `reply=안녕하세요` |
| `test_workflow_e2e.py` | ✅ 5 passed |
| `run_workflow_live_smoke.py` | ✅ 코드·문서 2시나리오 |
| `generate_irb_draft.py` | IRB 초안 생성 |

---

## 구현 완료

| 모듈 | 상태 |
|------|------|
| `llm/` | 5 Provider (LOCAL/OpenAI/Claude/Gemini/Azure) |
| `agents/` | Planner · Generator · Reviewer · Fixer |
| `orchestrator/workflow.py` | `AutoNoGaDaWorkflow` (Plan→Generate→Review→Fix) |
| `ontology/validator.py` | MED-SEM 20+ |

### 실증 사례

- 코드 자동화: patient_id/OD·OS 추출 함수
- 문서 자동화: 진단 요약 (DR/GL/AMD)
- MEDI 보고서: `POST /lab/fundus/report`

---

## 미완료 / 이슈

| 항목 | 상태 |
|------|------|
| `partner_four_agent_e2e.sh` | 409/500 — **`partner_e2e_inline.py`로 대체 예정** |
| Dashboard E2E | Vite `:5174` 기동 후 수동 확인 |

---

## 다음 우선순위

1. `partner_e2e_inline.py` 안정화 (실 fundus 이미지)
2. `run_lm_studio_four_agent_tests.sh` Docker 실 LLM
3. IRB 초안: `python3 scripts/generate_irb_draft.py`

---

## 스크립트

상세: `docs/SCRIPTS-REFERENCE.md`

```bash
LM_STUDIO_BASE_URL=http://192.168.0.12:1234/v1 PYTHONPATH=. \
  python3 scripts/test_lm_chat_wsl.py
PYTHONPATH=. python3 scripts/run_workflow_live_smoke.py
```
