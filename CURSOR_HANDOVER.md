# shared-libraries — Cursor Agent 인수인계

> 최종 업데이트: **2026-06-14** · Git: `1b6be32` → LLM 가이드 커밋 예정  
> **메타 HANDOVER**: `idea-collection/CURSOR_HANDOVER.md`

---

## LM Studio 상태: **OFF** (2026-06-14)

HDD 100% 사용으로 LM Studio 종료. LLM 호출 테스트·보고서·AutoNoGaDa는 **건너뜀**.

```
⚠️ LM Studio 켜주세요: port 1234, Serve on Local Network
```

| 환경 | URL (켜졌을 때) |
|------|-----------------|
| WSL | `http://192.168.0.12:1234/v1` |
| Docker | `http://host.docker.internal:1234/v1` |

**설정 SSOT**: `llm/.env.example` · `docs/LLM-SETUP-GUIDE.md`

---

## MEDI v10e (연동 현황)

| 항목 | 상태 |
|------|-----|
| GPU 훈련 | v10e 진행 중 (GL ep4=0.764) |
| v2_cache | 완료 |
| LLM 보고서 | LM Studio OFF → 대기 |

---

## 구현 완료

| 모듈 | 상태 |
|------|------|
| `llm/` | 5 Provider (LOCAL/OpenAI/Claude/Gemini/Azure) |
| `llm/.env.example` | 포트 **1234** 반영 ✅ |
| `docs/LLM-SETUP-GUIDE.md` | §1~§5 ✅ |
| `agents/` | Planner · Generator · Reviewer · Fixer |
| `orchestrator/workflow.py` | `AutoNoGaDaWorkflow` |

---

## LM Studio ON 후 검증

```bash
LM_STUDIO_BASE_URL=http://192.168.0.12:1234/v1 PYTHONPATH=. \
  python3 scripts/test_lm_chat_wsl.py
PYTHONPATH=. python3 scripts/run_workflow_live_smoke.py
```

---

## 참고

- SVG-Stock 실행 시 포트 **8000** 충돌 → LM Studio는 **1234** 사용
- `LLM_FALLBACK_ENABLED=true` 시 운영 Provider 실패 시 local 재시도
