# 4-에이전트 결정 시스템 — 목적·아키텍처·연동 테스트

> SSOT: `shared-libraries` · 롤백 태그 `before-four-agent-v1.0` · 최종 갱신 2026-05-23

---

## 1. 왜 도입했는가 (목적)

기존 **단일 ReviewerAgent**는 “통과/실패”를 한 관점에서만 판단합니다. 의료·IoT·결재 도메인에서는 **옹호(찬성)** 와 **비판(반대)** 근거를 분리해 기록하고, **Ontology 규칙**과 **도메인별 임계값**으로 최종 결정을 내리는 편이 감사·안전·롤백에 유리합니다.

| 목표 | 설명 |
|------|------|
| **안전한 실험** | 피처 플래그로 `legacy` ↔ `four_agent` 즉시 전환 |
| **롤백 가능** | Git 태그 + `rollback_four_agent.sh` + env 한 줄 |
| **감사 추적** | `audit_trail`에 advocate/critic 점수·요약·가중치 기록 |
| **점진 배포** | `ab_test` + `AGENT_FOUR_AGENT_ROLLOUT` (%) |
| **기존 코드 보존** | `ReviewerAgent` 삭제 없음 — Orchestrator PIPELINE Step 3에서만 분기 |

---

## 2. 아키텍처 (4 + 1)

```
생성물 (Generator 출력)
    ├─ AdvocateReviewer  → AdvocateReport (confidence, reasons)
    └─ CriticReviewer    → CriticReport (risk_score, issues)
              ↓
    OntologyValidator.mediate()  → MediationResult (final_score)
              ↓
    DecisionGate.decide()        → DecisionResult (APPROVE|REVISE|REJECT)
              ↓
    OrchestratorResult.decision_mode / audit_trail
```

**피처 플래그** (`agents/feature_flags.py`):

| `AGENT_DECISION_MODE` | 동작 |
|------------------------|------|
| `legacy` (기본) | 기존 `ReviewerAgent` |
| `four_agent` | Advocate + Critic + mediate + gate |
| `ab_test` | `request_id` 해시로 rollout % 분기 |

**연결 지점**: `Orchestrator._run_pipeline` Step 3 → `AgentPipeline.run_decision()`.

---

## 3. 연동 테스트의 의미 (무엇을 증명하는가)

연동 테스트는 **단위 테스트가 아닌 “실제 워크플로우에 꽂혔을 때”** 를 검증합니다.

### 3.1 계층별 역할

| 계층 | 파일 | 증명하는 것 |
|------|------|-------------|
| **단위** | `tests/test_four_agent_pipeline.py`, `test_ab_comparison.py`, `test_rollback.py` | 플래그·mediate·gate·A/B 분기·롤백 태그 |
| **Orchestrator 연동** | `tests/integration/test_orchestrator_four_agent.py`, `test_integration.py::TestOrchestratorFourAgent` | PIPELINE Step 3에서 four_agent 분기, `decision_mode`·`audit_trail` 전달, Fixer 루프 호환 |
| **LM Studio 실연동** | `@pytest.mark.requires_lm_studio` | Advocate/Critic 실제 LLM 호출 (CI 기본 skip) |
| **API E2E** | `test_rollback.py::test_existing_apis_unchanged` | MEDI/CoOps HTTP 엔드포인트 회귀 (스택 가동 시) |

### 3.2 Mock 연동 vs 실연동

| 모드 | env | 의미 |
|------|-----|------|
| **Mock 연동** | `AGENT_FOUR_AGENT_MOCK=1` | LLM 없이 결정론적 Advocate/Critic — **CI·로컬 기본**. Orchestrator·Pipeline **배선**이 맞는지 검증 |
| **실연동** | `AGENT_FOUR_AGENT_MOCK` unset + `LM_STUDIO_AVAILABLE=1` + ping 200 | HEAVY 모델로 JSON 리뷰 — **품질·지연** 검증 |

Mock 연동이 통과하면 “코드 경로·플래그·결과 매핑”이 안전하고, 실연동이 통과하면 “운영 LLM 환경에서도 동작”을 의미합니다.

### 3.3 통과 기준 (완료 정의)

- `legacy` 기본값에서 기존 Reviewer 경로 유지
- `four_agent`에서 `OrchestratorResult.decision_mode == "four_agent"` 및 `audit_trail.mode == "four_agent"`
- A/B 50% 분기 ±15% (`test_feature_flag_ab_split`)
- `before-four-agent-v1.0` 태그 존재
- 비교 리포트 `reports/four_agent_comparison_*.json` 생성 가능

---

## 4. 실행 방법

### 4.1 Mock 연동 (권장 · LM Studio 불필요)

```bash
cd projects/shared-libraries
export PYTHONPATH=.
export AGENT_FOUR_AGENT_MOCK=1
export AGENT_PIPELINE_LITE=1

python -m pytest \
  tests/integration/test_orchestrator_four_agent.py \
  tests/test_integration.py::TestOrchestratorFourAgent \
  tests/test_four_agent_pipeline.py \
  tests/test_ab_comparison.py \
  tests/test_rollback.py \
  -v
```

### 4.2 LM Studio 실연동

```bash
# LM Studio: Server 0.0.0.0:8000, GET /v1/models → 200
export LM_STUDIO_AVAILABLE=1
export AGENT_DECISION_MODE=four_agent
unset AGENT_FOUR_AGENT_MOCK

python -m pytest tests/integration/test_orchestrator_four_agent.py \
  --lm-studio-required -v -k lm_studio
```

### 4.3 비교 리포트

```bash
python scripts/generate_comparison_report.py
# → reports/four_agent_comparison_YYYYMMDD.json
```

### 4.4 운영 env (`projects/.env.local`)

```env
AGENT_DECISION_MODE=legacy          # legacy | four_agent | ab_test
AGENT_FOUR_AGENT_ROLLOUT=0          # ab_test 시 0~100
AGENT_AUDIT_TRAIL_ENABLED=true
```

---

## 5. 롤백

```bash
# 1) env만 (가장 빠름)
AGENT_DECISION_MODE=legacy

# 2) 스크립트
bash projects/shared-libraries/scripts/rollback_four_agent.sh

# 3) Git 태그
git checkout before-four-agent-v1.0 -- agents/reviewer.py ontology/validator.py agents/pipeline.py agents/orchestrator.py
```

---

## 6. 최근 연동 테스트 결과 (2026-05-23)

| 구분 | 결과 |
|------|------|
| Mock 연동 (Orchestrator + Pipeline + A/B + 롤백 + Harness decision) | **15+ passed** |
| LM Studio `--lm-studio-required` | **3 passed** (HTTP 404 → Advocate/Critic **mock fallback**, 실 LLM 미연결) |
| MEDI Lab E2E (`medi_four_agent_e2e_smoke.sh`) | **200 OK** · `decision_mode=legacy` (컨테이너 env 기본값) |
| `agreement_rate` (10케이스 A/B) | **0.80** |
| 권장 | **`gradual_rollout`** (`ab_test` + ROLLOUT 10%부터) |

---

## 7. 관련 파일

| 경로 | 역할 |
|------|------|
| `agents/feature_flags.py` | 모드·rollout |
| `agents/pipeline.py` | `run_decision()` |
| `agents/orchestrator.py` | PIPELINE Step 3 분기 |
| `agents/decision_gate.py` | 도메인별 임계값 |
| `agents/reviewer.py` | `ReviewerAgent` + `AdvocateReviewer` + `CriticReviewer` |
| `ontology/validator.py` | `mediate()` |
| `harness/scenarios/decision_scenarios.json` | Harness DECISION 시나리오 |
| `scripts/rollback_four_agent.sh` | 롤백 |
| `scripts/generate_comparison_report.py` | Legacy vs 4-agent 리포트 |

메타 문서: `CURSOR_HANDOVER.md` §「4-에이전트 결정 시스템」· `book/appendix/env-reference.md` §4-에이전트.
