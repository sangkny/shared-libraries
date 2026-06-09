# shared-libraries — Cursor Agent 인수인계

> 최종 업데이트: 2026-06-09  
> **3-플랫폼 통합**: `MEDI-IOT-EyeCare/docs/PLATFORM-OVERVIEW.md`  
> **메타 HANDOVER**: `idea-collection/CURSOR_HANDOVER.md`

---

## 역할 — 3-플랫폼 공통 DNA

`shared-libraries`는 **MEDI-IOT · AutoNoGaDa · CoOps**가 공유하는 Python 패키지입니다.  
각 SaaS는 `PYTHONPATH` 또는 Docker volume으로 import합니다.

```
shared-libraries/
├── llm/           # 5 Provider 추상화
├── agents/        # Planner·Generator·Reviewer·Fixer
├── ontology/      # Semantic/Structural Validator
├── auth/          # JWT · RBAC
├── saas/          # Billing · Stripe · Quota
├── notifications/ # FCM · APNs · Inbox
├── harness/       # 통합 시나리오
└── observability/ # Prometheus · decision_metrics
```

---

## 소비자별 연동 현황

| 모듈 | MEDI-IOT | AutoNoGaDa | CoOps |
|------|----------|------------|-------|
| `llm` | diagnosis explain · vision | 코드 생성 | IR/Video/SNS |
| `agents` | four_agent decision | ADK 파이프라인 | 결재 문서 |
| `ontology` | MED-SEM 20+ | CODE 룰 | BUSINESS 룰 |
| `auth` | JWT · clinical RBAC | API 인증 | 모바일 JWT |
| `saas` | medi billing · Stripe | ADK billing | coops billing |
| `notifications` | — | — | inbox · push hook |
| `harness` | MEDI 시나리오 | ADK 시나리오 | CoOps E2E |

---

## AutoNoGaDa 활용 사례 (shared 기반)

AutoNoGaDa-ADK는 shared-libraries를 **최대 활용**하는 SaaS입니다.

| 활용 | shared 모듈 | 결과 |
|------|-------------|------|
| 코드 리뷰·생성 | `agents` + `ontology`(CODE) | MEDI 134 unit 유지 |
| LLM 전환 | `llm` Provider factory | LOCAL ↔ cloud 무중단 |
| 과금 | `saas` StripeService | ADK checkout/webhook |
| 회귀 | `harness` scenarios | 45 시나리오 |

**실증**: shared 없이는 MEDI 5질환·v10·문서 250페이지 규모를 수 주에 구축 불가

---

## CoOps 연동 현황

| 기능 | shared 모듈 | API |
|------|-------------|-----|
| Push | `notifications.gateway` | FCM/APNs/Expo |
| Inbox | `notifications` InboxService | `GET /inbox` |
| Billing | `saas` make_billing_models | Stripe R2/R3 |
| 결재 hook | `_notify_safe` | approvals 3 routes |
| MEDI 연동 | (HTTP client) | `mediApi` reviews |

모바일 R3: FCM OAuth2 · APNs ES256 JWT 1h 캐시 · retention loop

---

## MEDI-IOT 연동 현황

| 기능 | shared 모듈 |
|------|-------------|
| 4-agent diagnosis | `agents` · `observability/decision_metrics` |
| 의료 ontology | `ontology` MED-SEM |
| Partner audit | `audit_trail` in lab API |
| Billing | `saas` medi prefix tables |
| Auth | `auth` dependencies |

`AGENT_DECISION_MODE=four_agent` ROLLOUT=100% · A/B agreement 100%

---

## 테스트·회귀

```bash
cd projects/shared-libraries
python -m pytest tests/ -q

# Tier 0 (LM Studio 불필요)
cd idea-collection && bash scripts/check-ontology-harness.sh
```

| 스냅샷 | PASS |
|--------|------|
| shared unit | 87+ |
| + CoOps | 68+ |
| + MEDI (via compose) | 134 unit |

---

## 다음 우선순위

1. CoOps M4 — 모바일 fundus → MEDI comprehensive API
2. ontology MED-SEM — v10b 성능 연동
3. harness — 3-플랫폼 E2E 시나리오 추가

---

## 관련 문서

- `book/part3/ch07~ch10` — shared-libraries 구현 상세
- `book/part1/ch01-platform-overview.md` — 3-플랫폼 개요
- `book/part7/ch46-business-strategy.md` — 사업화
