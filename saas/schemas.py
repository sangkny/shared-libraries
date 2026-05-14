"""shared-libraries/saas/schemas — Pydantic 응답·요청 스키마.

ADK / CoOps / 향후 MEDI 모두 동일 응답 구조 — 클라이언트 (admin 대시보드 /
랜딩 페이지) 가 single SDK 로 멀티 도메인 SaaS 를 다룰 수 있도록.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PlanOut(BaseModel):
    """Plan 카탈로그 응답."""

    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    price_usd_per_month: float
    monthly_call_quota: int | None
    allowed_models: list[str]
    description: str | None = None
    is_active: bool = True


class PlanListResponse(BaseModel):
    plans: list[PlanOut]
    default_code: str = "free"


class SubscriptionOut(BaseModel):
    """현재 활성 구독 정보."""

    model_config = ConfigDict(from_attributes=True)

    plan_code: str
    plan_name: str
    monthly_call_quota: int | None
    allowed_models: list[str]
    started_at: datetime
    current_period_end: datetime | None = None


class UsageSnapshot(BaseModel):
    """월 단위 사용량 스냅샷."""

    year_month: str = Field(..., description="YYYY-MM")
    calls_used: int = 0
    calls_limit: int | None = None
    calls_remaining: int | None = None
    quota_pct: float = 0.0
    tokens_total: int = 0
    cost_usd: float = 0.0


class MeResponse(BaseModel):
    user_id: str
    role: str
    subscription: SubscriptionOut
    usage: UsageSnapshot


class SubscribeRequest(BaseModel):
    """admin 전용 — 사용자에게 plan 부여. ``plan_code`` regex 는 서비스에서 override."""

    user_id: str = Field(..., min_length=1, max_length=128)
    plan_code: str = Field(..., min_length=1, max_length=32)


class SubscribeResponse(BaseModel):
    user_id: str
    plan_code: str
    previous_plan_code: str | None = None
    started_at: datetime


class OnboardBatchRequest(BaseModel):
    """admin — 베타 고객 일괄 plan 부여 (C Week 3 Day 5).

    동일 ``plan_code`` 를 N 명에게 한 번에 부여. 부분 실패는 응답의 ``failed``
    리스트로 보고하며 성공한 항목은 commit 된다.
    """

    user_ids: list[str] = Field(
        ..., min_length=1, max_length=100,
        description="대상 user_id 목록 (1~100명)",
    )
    plan_code: str = Field(..., min_length=1, max_length=32)
    welcome_note: str | None = Field(
        None, max_length=2000,
        description="welcome email/Slack 본문 (선택, 응답에 포함되어 운영자가 발송)",
    )


class OnboardBatchEntry(BaseModel):
    user_id: str
    plan_code: str
    previous_plan_code: str | None = None
    status: str = Field(..., description="ok | failed")
    error: str | None = None


class OnboardBatchResponse(BaseModel):
    plan_code: str
    requested: int
    succeeded: int
    failed: int
    entries: list[OnboardBatchEntry]
    welcome_note: str | None = None
    issued_at: datetime


class UsageTimelinePoint(BaseModel):
    date: str
    calls: int
    tokens: int


class UsageTimelineResponse(BaseModel):
    user_id: str
    days: int
    points: list[UsageTimelinePoint]


class UsageHistoryEntry(BaseModel):
    year_month: str
    calls_count: int
    tokens_total: int
    cost_usd: float


class UsageHistoryResponse(BaseModel):
    user_id: str
    history: list[UsageHistoryEntry]


class PlanDistributionEntry(BaseModel):
    plan_code: str
    plan_name: str
    active_subscribers: int
    price_usd_per_month: float
    monthly_revenue_usd: float


class AdminStatsResponse(BaseModel):
    year_month: str
    total_active_subscribers: int
    total_monthly_revenue_usd: float
    total_calls_this_month: int
    total_tokens_this_month: int
    plan_distribution: list[PlanDistributionEntry]


# ── Stripe (B-7) ─────────────────────────────────────────────


class StripeCheckoutRequest(BaseModel):
    plan_code: str = Field(..., min_length=1, max_length=32)
    success_url: str | None = Field(default=None, max_length=500)
    cancel_url: str | None = Field(default=None, max_length=500)
    allow_promotion_codes: bool = False
    promotion_code: str | None = Field(
        default=None, max_length=128,
        description="Stripe Promotion Code id (promo_...), coupon_id 와 동시 사용 금지",
    )
    coupon_id: str | None = Field(
        default=None, max_length=128,
        description="Stripe Coupon id (coupon_...)",
    )

    @model_validator(mode="after")
    def _discount_exclusive(self) -> "StripeCheckoutRequest":
        if self.promotion_code and self.coupon_id:
            raise ValueError("promotion_code 과 coupon_id 는 동시에 사용할 수 없습니다")
        return self


class StripeCheckoutResponse(BaseModel):
    session_id: str
    url: str


class StripePlanMappingRequest(BaseModel):
    plan_code: str = Field(..., min_length=1, max_length=32)
    stripe_price_id: str = Field(..., min_length=4, max_length=128)


class StripePlanMappingOut(BaseModel):
    plan_code: str
    stripe_price_id: str


class StripeWebhookResponse(BaseModel):
    received: bool
    type: str | None = None
    action: str | None = None


class StripeStatusResponse(BaseModel):
    enabled: bool
    public_key: str | None = None
    supported_events: list[str]


# ── Stripe Customer Portal (B-7 Round 2) ─────────────────────


class StripePortalRequest(BaseModel):
    """Customer Portal session 생성 요청 — 활성 sub 1개 가정."""

    return_url: str | None = Field(default=None, max_length=500)
    flow: str | None = Field(
        default=None,
        max_length=32,
        description="invoice_history — 인보이스 목록 화면 직행 (모바일 return_url 권장)",
    )


class StripePortalResponse(BaseModel):
    session_id: str
    url: str


# ── Stripe metered usage (B-7 Round 3) ───────────────────────────


class StripeMeteredUsageRequest(BaseModel):
    quantity: float = Field(..., ge=0, le=1e15)
    action: Literal["increment", "set"] = "increment"
    timestamp: int | None = Field(
        default=None,
        description="Unix 시각 (선택). 미지정 시 Stripe 기본.",
    )


class StripeMeteredUsageResponse(BaseModel):
    status: str
    usage_record_id: str | None = None
    subscription_item_id: str | None = None
    stripe_subscription_id: str | None = None
