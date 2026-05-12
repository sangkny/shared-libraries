"""shared-libraries/saas/quota — Quota Enforcement + Usage Metering.

각 서비스 (ADK, CoOps) 가 자기 ``BillingService`` 인스턴스 + auth deps 함수 +
DB deps 함수를 주입해 FastAPI deps factory 를 생성::

    from shared_libraries.saas import make_enforce_quota_dep
    from services.billing import billing_service  # 서비스별 인스턴스
    from auth.dependencies import current_user_strict
    from database import get_db

    enforce_quota = make_enforce_quota_dep(
        billing_service,
        get_user=current_user_strict,
        get_db=get_db,
    )

    # api/pipeline.py
    @router.post("/generate")
    async def generate(
        ...,
        quota: QuotaContext = Depends(enforce_quota("generate")),
    ):
        ...
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import current_year_month, next_month_reset_ts, quota_headers
from .service import BillingService

log = logging.getLogger("saas.quota")


def _emit_saas_call(
    service_name: str | None, plan_code: str, action: str, *, success: bool
) -> None:
    """Prometheus ``saas_calls_total`` 카운터 (best-effort, no-op if missing)."""
    if not service_name:
        return
    try:
        from observability import inc_saas_call

        inc_saas_call(
            service=service_name,
            plan_code=plan_code,
            action=action,
            success=success,
        )
    except Exception:
        pass


def _emit_quota_blocked(
    service_name: str | None, plan_code: str, action: str
) -> None:
    """Prometheus ``saas_quota_blocked_total`` 카운터 (best-effort)."""
    if not service_name:
        return
    try:
        from observability import inc_saas_quota_blocked

        inc_saas_quota_blocked(
            service=service_name, plan_code=plan_code, action=action
        )
    except Exception:
        pass


@dataclass
class QuotaContext:
    """라우트 핸들러가 받는 quota 컨텍스트."""

    user_id: str
    role: str
    action: str
    plan_code: str
    monthly_call_quota: int | None
    allowed_models: str
    calls_used_before: int


def make_enforce_quota_dep(
    billing: BillingService,
    *,
    get_user: Callable[..., Awaitable[dict]],
    get_db: Callable[..., Awaitable[AsyncSession]],
) -> Callable[[str], Callable[..., Awaitable[QuotaContext]]]:
    """FastAPI dependency factory 의 *팩토리*.

    Returns ``enforce_quota(action: str)`` 함수 — 사용 시 ``Depends(enforce_quota("generate"))``.

    설계 결정 (B-3, 2026-05-12):
        - 호출 *전* 한도 *확인* 만 (count 증가 X). 호출 *후* 기록은 ``record_call``.
        - 무제한 plan (Ent) 은 ``X-RateLimit-Limit: -1``.
        - ``X-RateLimit-Reset`` 은 다음 달 1일 00:00 UTC Unix timestamp.
    """

    def enforce_quota(action: str) -> Callable[..., Awaitable[QuotaContext]]:
        async def _dep(
            response: Response,
            db: AsyncSession = Depends(get_db),
            user: dict = Depends(get_user),
        ) -> QuotaContext:
            user_id = user["user_id"]
            sub, plan = await billing.get_or_create_active_subscription(db, user_id)
            monthly = await billing.get_or_create_monthly_usage(db, user_id)
            used = int(monthly.calls_count or 0)
            quota = plan.monthly_call_quota
            reset_ts = next_month_reset_ts()
            headers = quota_headers(quota, used, reset_ts=reset_ts)

            if quota is not None and used >= int(quota):
                log.info(
                    "quota_exceeded user_id=%s action=%s plan=%s used=%d limit=%d",
                    user_id,
                    action,
                    plan.code,
                    used,
                    int(quota),
                )
                _emit_quota_blocked(
                    getattr(billing, "service_name", None), plan.code, action
                )
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail={
                        "error": "quota_exceeded",
                        "message": (
                            f"월 호출 한도 초과 (plan={plan.code}, used={used}/{int(quota)}). "
                            f"plan 업그레이드 또는 다음 달까지 대기."
                        ),
                        "plan_code": plan.code,
                        "calls_used": used,
                        "calls_limit": int(quota),
                        "year_month": current_year_month(),
                        "upgrade_hint": (
                            "POST /api/v1/billing/subscribe (admin) "
                            "또는 customer portal (백로그)"
                        ),
                    },
                    headers=headers,
                )

            for hk, hv in headers.items():
                response.headers[hk] = hv
            return QuotaContext(
                user_id=user_id,
                role=user.get("role", ""),
                action=action,
                plan_code=plan.code,
                monthly_call_quota=quota,
                allowed_models=plan.allowed_models or "FAST",
                calls_used_before=used,
            )

        return _dep

    return enforce_quota


async def record_call(
    billing: BillingService,
    db: AsyncSession,
    quota: QuotaContext,
    *,
    success: bool,
    model_used: str | None = None,
    tokens_estimated: int = 0,
    latency_ms: int | None = None,
) -> None:
    """호출 종료 후 사용량을 기록 (B-4 동등).

    - ``BillingUsageRecord`` 에 호출 단위 row 추가 (감사용, 실패도 기록).
    - **성공 시에만** ``MonthlyUsage.calls_count += 1``, ``tokens_total += tokens``.
    - 모든 예외는 catch + 로그만 (응답 후 사용량 기록 실패가 클라이언트로 전파 X).
    """
    try:
        rec = billing.UsageRecord(
            id=str(uuid.uuid4()),
            user_id=quota.user_id,
            action=quota.action,
            plan_code=quota.plan_code,
            tokens_estimated=int(tokens_estimated or 0),
            model_used=model_used,
            success=bool(success),
            latency_ms=int(latency_ms) if latency_ms is not None else None,
        )
        db.add(rec)

        if success:
            monthly = await billing.get_or_create_monthly_usage(db, quota.user_id)
            monthly.calls_count = int(monthly.calls_count or 0) + 1
            monthly.tokens_total = int(monthly.tokens_total or 0) + int(
                tokens_estimated or 0
            )
        await db.flush()
        _emit_saas_call(
            getattr(billing, "service_name", None),
            quota.plan_code,
            quota.action,
            success=success,
        )
    except Exception:
        log.exception(
            "record_call 실패 user_id=%s action=%s — 사용량 기록 누락",
            quota.user_id,
            quota.action,
        )


__all__ = ["QuotaContext", "make_enforce_quota_dep", "record_call"]
