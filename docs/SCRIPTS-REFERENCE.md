# shared-libraries 스크립트 레퍼런스

> 최종 업데이트: 2026-06-11 (LM Studio **:1234**)

## 4-에이전트 / LM Studio

| 스크립트 | 용도 | LM Studio |
|----------|------|-----------|
| `scripts/probe_lm_studio.py` | `/v1/models` 프로브 · URL 자동 감지 | 선택 |
| `scripts/test_lm_chat_wsl.py` | WSL→Windows LM Studio **chat** 스모크 | **필요** |
| `scripts/run_workflow_live_smoke.py` | AutoNoGaDaWorkflow 코드·문서 2시나리오 | **필요** |
| `scripts/generate_irb_draft.py` | IRB 연구계획서 초안 → `/tmp/irb_draft.txt` | **필요** |
| `scripts/run_lm_studio_four_agent_tests.sh` | Docker `shared-libs-dev`에서 실 LLM 통합 테스트 | **필요** |
| `scripts/medi_four_agent_e2e_smoke.sh` | MEDI fundus comprehensive · legacy vs four_agent | 불필요 |
| `scripts/partner_four_agent_e2e.sh` | Partner API register+analyze · audit_trail | 불필요 |
| `scripts/partner_e2e_inline.py` | Partner E2E (Python · 컨테이너 내부 실행) | 불필요 |
| `scripts/rollback_four_agent.sh` | `before-four-agent-v1.0` 롤백 + legacy 모드 | 불필요 |

## AutoNoGaDa / Git

| 스크립트 | 용도 |
|----------|------|
| `scripts/do_commit_sl.sh` | shared-libraries **지정 파일만** stage·commit |
| `scripts/apply_gradual_rollout.sh` | four_agent gradual rollout |
| `scripts/rollout_100_checklist.py` | ROLLOUT=100 체크리스트 |

## 분석 결과 (2026-06-11)

### `do_commit_sl.sh`
- **역할**: shared-libraries 전용 안전 커밋 헬퍼
- **사용**: `bash scripts/do_commit_sl.sh "feat: ..." orchestrator/workflow.py tests/`

### `partner_four_agent_e2e.sh`
- **역할**: Partner fundus analyze HTTP E2E · `patient-rollout-3`로 four_agent 버킷 검증
- **전제**: MEDI API `:8001` · `AGENT_DECISION_MODE=four_agent`

### `test_lm_chat_wsl.py`
- **역할**: `probe_lm_studio`로 URL 찾은 뒤 `LLMClient.chat()` 1회 호출
- **WSL**: Windows `curl.exe` / `127.0.0.1:8000` 폴백 지원

## LM Studio URL (2026-06-11)

| 환경 | URL |
|------|-----|
| WSL | `http://192.168.0.12:1234/v1` |
| Docker | `http://host.docker.internal:1234/v1` |

> SVG-Stock `:8000` 충돌 — LM Studio는 **1234** 사용. `projects/docs/NETWORK-GUIDE.md`

## 관련 문서

- `projects/docs/NETWORK-GUIDE.md`
- `book/part7/ch31-four-agent-decision.md`
- `orchestrator/workflow.py` — AutoNoGaDaWorkflow
