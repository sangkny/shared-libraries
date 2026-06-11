# shared-libraries — Cursor Agent 인수인계

> 최종 업데이트: 2026-06-11  
> **메타 HANDOVER**: `idea-collection/CURSOR_HANDOVER.md`

---

## LM Studio 실연동 (2026-06-11) ✅

| URL | 환경 |
|-----|------|
| `http://192.168.0.12:1234/v1` | WSL |
| `http://host.docker.internal:1234/v1` | Docker |

| 검증 | 결과 |
|------|------|
| `test_lm_chat_wsl.py` | ✅ `reply=안녕하세요` |
| `test_workflow_e2e.py` | ✅ 5 passed (mock) |
| `run_workflow_live_smoke.py` | 코드·문서 2시나리오 |
| `generate_irb_draft.py` | IRB 초안 |

**모델**: gemma-4-26b · gemma-4-e4b · mistral-7b · nomic-embed

```bash
LM_STUDIO_BASE_URL=http://192.168.0.12:1234/v1 PYTHONPATH=. \
  python3 scripts/test_lm_chat_wsl.py
PYTHONPATH=. python3 scripts/run_workflow_live_smoke.py
bash scripts/run_lm_studio_four_agent_tests.sh
```

---

## 다음 우선순위

1. **MEDI** `POST /lab/fundus/report` E2E
2. **Dashboard** 브라우저 체크리스트 (`MEDI/docs/BROWSER-E2E-CHECKLIST.md`)
3. **SaMD IRB** 제출 패키지

---

## Git

`f7e6916`+ · LM Studio 1234 · `deb0160` Workflow
