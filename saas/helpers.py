"""shared-libraries/saas/helpers — Plan-Quota 공통 헬퍼.

라우트·서비스 모두에서 import 가능한 순수 함수만. DB·ORM 의존 X.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

DEFAULT_FREE_PLAN_CODE = "free"


def current_year_month(now: datetime | None = None) -> str:
    """``YYYY-MM`` 문자열 (UTC 기준) — 청구·집계 키."""
    d = (now or datetime.now(timezone.utc)).date()
    return d.strftime("%Y-%m")


def next_month_reset_ts(now: datetime | None = None) -> int:
    """다음 달 1일 00:00 UTC 의 Unix timestamp — ``X-RateLimit-Reset`` 헤더."""
    n = now or datetime.now(timezone.utc)
    year = n.year + (1 if n.month == 12 else 0)
    month = 1 if n.month == 12 else n.month + 1
    reset = datetime(year, month, 1, 0, 0, 0, tzinfo=timezone.utc)
    return int(reset.timestamp())


def quota_headers(
    quota: int | None, used: int, *, reset_ts: int | None = None
) -> dict[str, str]:
    """표준 ``X-RateLimit-*`` 헤더 dict. ``quota=None`` 이면 무제한 (=-1)."""
    if quota is None:
        limit_s = "-1"
        remaining_s = "-1"
    else:
        limit_s = str(int(quota))
        remaining_s = str(max(0, int(quota) - int(used)))
    return {
        "X-RateLimit-Limit": limit_s,
        "X-RateLimit-Remaining": remaining_s,
        "X-RateLimit-Reset": str(reset_ts or next_month_reset_ts()),
    }


def parse_allowed_models(allowed_models: str) -> list[str]:
    """``"FAST,HEAVY"`` → ``["FAST", "HEAVY"]``."""
    return [tok.strip() for tok in (allowed_models or "").split(",") if tok.strip()]


def usage_snapshot_dict(monthly: Any, plan: Any) -> dict[str, Any]:
    """``UsageSnapshot`` Pydantic 스키마용 dict 변환.

    ``monthly`` 는 ``BillingMonthlyUserUsage`` 인스턴스, ``plan`` 은
    ``BillingPlan`` 인스턴스 (덕 타이핑).
    """
    quota = plan.monthly_call_quota
    used = int(monthly.calls_count or 0)
    if quota is None:
        remaining: int | None = None
        pct = 0.0
    else:
        remaining = max(0, int(quota) - used)
        pct = (used / int(quota) * 100.0) if int(quota) > 0 else 0.0
    return {
        "year_month": monthly.year_month,
        "calls_used": used,
        "calls_limit": int(quota) if quota is not None else None,
        "calls_remaining": remaining,
        "quota_pct": round(float(pct), 2),
        "tokens_total": int(monthly.tokens_total or 0),
        "cost_usd": float(monthly.cost_usd or 0),
    }


__all__ = [
    "DEFAULT_FREE_PLAN_CODE",
    "current_year_month",
    "next_month_reset_ts",
    "quota_headers",
    "parse_allowed_models",
    "usage_snapshot_dict",
]
