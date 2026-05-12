"""shared-libraries/saas — 멀티 도메인 SaaS billing/quota 공통 모듈.

ADK 와 CoOps 가 동일한 SaaS 패턴 (Plan / Subscription / Quota / Usage) 을
공유하면서도 **각 서비스의 테이블·alembic 은 격리**되도록 설계.

설계 결정 (2026-05-12, C-Step 2):
    - **ORM 클래스는 factory 로 생성** — ``make_billing_models(Base, table_prefix="")``
      가 4종 ORM 클래스를 반환 (테이블명에 prefix 적용). ADK 는 prefix="" 로
      기존 ``billing_plans`` 그대로, CoOps 는 ``coops_`` prefix.
    - **BillingService 는 ORM 4종을 DI** — 덕 타이핑 인터페이스. 각 서비스가
      자기 ORM 클래스로 ``BillingService`` 인스턴스 생성.
    - **enforce_quota 는 factory** — ``make_enforce_quota_dep(service, get_user, get_db)``
      가 FastAPI deps 함수를 반환. service 별 auth 의존성 주입.
    - **Pydantic 스키마는 공유** — Plan/Subscription/Usage 응답 구조는 동일.
"""
from __future__ import annotations

from .billing_models import make_billing_models
from .helpers import (
    DEFAULT_FREE_PLAN_CODE,
    current_year_month,
    next_month_reset_ts,
    parse_allowed_models,
    quota_headers,
)
from .quota import QuotaContext, make_enforce_quota_dep, record_call
from .schemas import (
    AdminStatsResponse,
    MeResponse,
    OnboardBatchEntry,
    OnboardBatchRequest,
    OnboardBatchResponse,
    PlanDistributionEntry,
    PlanListResponse,
    PlanOut,
    StripeCheckoutRequest,
    StripeCheckoutResponse,
    StripePlanMappingOut,
    StripePlanMappingRequest,
    StripeStatusResponse,
    StripeWebhookResponse,
    SubscribeRequest,
    SubscribeResponse,
    SubscriptionOut,
    UsageHistoryEntry,
    UsageHistoryResponse,
    UsageSnapshot,
    UsageTimelinePoint,
    UsageTimelineResponse,
)
from .service import BillingService
from .stripe_models import make_stripe_models
from .stripe_service import (
    SUPPORTED_EVENTS as STRIPE_SUPPORTED_EVENTS,
    StripeConfig,
    StripeDisabled,
    StripeService,
    StripeSignatureError,
)

__all__ = [
    "make_billing_models",
    "make_stripe_models",
    "BillingService",
    "StripeConfig",
    "StripeService",
    "StripeDisabled",
    "StripeSignatureError",
    "STRIPE_SUPPORTED_EVENTS",
    "QuotaContext",
    "make_enforce_quota_dep",
    "record_call",
    "DEFAULT_FREE_PLAN_CODE",
    "current_year_month",
    "next_month_reset_ts",
    "parse_allowed_models",
    "quota_headers",
    # Pydantic
    "PlanOut",
    "PlanListResponse",
    "SubscriptionOut",
    "UsageSnapshot",
    "MeResponse",
    "SubscribeRequest",
    "SubscribeResponse",
    "OnboardBatchRequest",
    "OnboardBatchEntry",
    "OnboardBatchResponse",
    "UsageHistoryEntry",
    "UsageHistoryResponse",
    "UsageTimelinePoint",
    "UsageTimelineResponse",
    "PlanDistributionEntry",
    "AdminStatsResponse",
    "StripeCheckoutRequest",
    "StripeCheckoutResponse",
    "StripePlanMappingRequest",
    "StripePlanMappingOut",
    "StripeStatusResponse",
    "StripeWebhookResponse",
]
