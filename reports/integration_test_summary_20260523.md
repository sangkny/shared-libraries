# 4-에이전트 연동 테스트 실행 요약

**실행일**: 2026-05-23  
**환경**: WSL · `PYTHONPATH=.` · `AGENT_FOUR_AGENT_MOCK=1`

## 결과

| 스위트 | passed | skipped | failed |
|--------|--------|---------|--------|
| `tests/integration/test_orchestrator_four_agent.py` | 3 | 1 | 0 |
| `tests/test_integration.py::TestOrchestratorFourAgent` | 1 | 1 | 0 |
| `tests/test_rollback.py` | 3 | 1 | 0 |
| `tests/test_four_agent_pipeline.py` | 3 | 0 | 0 |
| `tests/test_ab_comparison.py` | 2 | 0 | 0 |
| **합계** | **12** | **7** | **0** |

## Skip 사유

| 테스트 | 사유 |
|--------|------|
| `*_lm_studio` | `LM_STUDIO_AVAILABLE=1` + `GET /v1/models` 200 미충족 (호스트 :8000 → 404) |
| `test_existing_apis_unchanged` | MEDI API `401` (Bearer/스택 미설정) |

## A/B 비교 리포트

- 파일: `reports/four_agent_comparison_20260523.json`
- `agreement_rate`: **0.80**
- 불일치 1건: medical 경계값 — legacy APPROVE vs four_agent REVISE

## 재실행

```bash
cd projects/shared-libraries
export PYTHONPATH=. AGENT_FOUR_AGENT_MOCK=1 AGENT_PIPELINE_LITE=1
python -m pytest tests/integration/ tests/test_integration.py::TestOrchestratorFourAgent \
  tests/test_four_agent_pipeline.py tests/test_ab_comparison.py tests/test_rollback.py -v
```
