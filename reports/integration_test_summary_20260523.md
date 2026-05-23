# 4-에이전트 연동 테스트 실행 요약 (최종)

**실행일**: 2026-05-23  
**LM Studio**: `http://127.0.0.1:8000/v1` (Windows) · Docker `host.docker.internal:8000/v1`

## 결과

| 스위트 | passed | skipped | 비고 |
|--------|--------|---------|------|
| Mock (Orchestrator·Pipeline·A/B·롤백) | 15+ | 0 | `AGENT_FOUR_AGENT_MOCK=1` |
| LM Studio 실연동 (Docker) | **4** | 0 | Advocate/Critic FAST · JSON 파싱 |
| Harness `decision` | 5/5 | 0 | pass_rate 100%, agreement 80% (mock) |
| MEDI E2E smoke | **PASS** | — | `patient-rollout-3` → four_agent · a/b/c → legacy |
| A/B 리포트 (10케이스) | — | — | `agreement_rate` **1.00**, `four_agent` |
| 의료 불일치 | **0건** | — | `_medical_mock_profile` 튜닝 |
| ROLLOUT | **100%** | — | `AGENT_DECISION_MODE=four_agent` |
| Partner E2E | PASS | — | `partner_e2e_inline.py` fundus |
| Grafana | — | — | `decision_metrics` on `/metrics/prometheus` |

## 운영 적용

- `AGENT_DECISION_MODE=ab_test`
- `AGENT_FOUR_AGENT_ROLLOUT=10`
- `docker-compose.dev.yml` → **`env_file: .env.local` only** (`AGENT_*`를 `environment`에 넣지 않음)
- `apply_gradual_rollout.sh` + `medi-iot-api-dev printenv` 확인
- MEDI: `patient-rollout-3` → `decision_mode=four_agent`, `decision=REVISE`

## 재실행

```bash
bash shared-libraries/scripts/run_lm_studio_four_agent_tests.sh
bash shared-libraries/scripts/apply_gradual_rollout.sh
docker exec medi-iot-api-dev printenv | grep AGENT
bash shared-libraries/scripts/medi_four_agent_e2e_smoke.sh
docker exec shared-libs-dev python -m harness decision
```
