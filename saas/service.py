"""shared-libraries/saas/service — 멀티 도메인 SaaS BillingService.

각 서비스 (ADK, CoOps) 가 자기 ORM 4종을 DI 로 주입해 인스턴스 생성::

    from shared_libraries.saas import BillingService
    from models.billing import (
        BillingPlan, BillingSubscription, BillingUsageRecord, BillingMonthlyUserUsage,
    )

    billing = BillingService(
        plan_cls=BillingPlan,
        subscription_cls=BillingSubscription,
        usage_record_cls=BillingUsageRecord,
        monthly_usage_cls=BillingMonthlyUserUsage,
    )
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import (
    DEFAULT_FREE_PLAN_CODE,
    current_year_month,
    parse_allowed_models,
    usage_snapshot_dict,
)

log = logging.getLogger("saas.service")


class BillingService:
    """덕 타이핑 ORM (Plan/Subscription/UsageRecord/MonthlyUsage) DI 기반 SaaS 빌링.

    ORM 클래스는 다음 attribute 를 가져야 한다 (덕 타이핑):

    Plan: ``id, code, name, price_usd_per_month, monthly_call_quota, allowed_models,
          description, is_active``

    Subscription: ``id, user_id, plan_id, status, started_at, current_period_end,
                  cancelled_at``

    UsageRecord: ``id, user_id, action, plan_code, tokens_estimated, model_used,
                 success, latency_ms, created_at``

    MonthlyUsage: ``id, user_id, year_month, calls_count, tokens_total, cost_usd,
                  last_updated``
    """

    def __init__(
        self,
        *,
        plan_cls,
        subscription_cls,
        usage_record_cls,
        monthly_usage_cls,
        default_free_code: str = DEFAULT_FREE_PLAN_CODE,
    ) -> None:
        self.Plan = plan_cls
        self.Subscription = subscription_cls
        self.UsageRecord = usage_record_cls
        self.MonthlyUsage = monthly_usage_cls
        self.default_free_code = default_free_code

    # ── 카탈로그 ────────────────────────────────────────────────────

    async def get_plan_by_code(self, db: AsyncSession, code: str):
        return await db.scalar(select(self.Plan).where(self.Plan.code == code))

    async def list_active_plans(self, db: AsyncSession) -> list[Any]:
        rows = await db.execute(
            select(self.Plan)
            .where(self.Plan.is_active.is_(True))
            .order_by(self.Plan.price_usd_per_month)
        )
        return list(rows.scalars().all())

    # ── Subscription ───────────────────────────────────────────────

    async def get_active_subscription(self, db: AsyncSession, user_id: str):
        return await db.scalar(
            select(self.Subscription)
            .where(self.Subscription.user_id == user_id)
            .where(self.Subscription.status == "active")
            .order_by(self.Subscription.started_at.desc())
            .limit(1)
        )

    async def get_or_create_active_subscription(
        self, db: AsyncSession, user_id: str
    ) -> tuple[Any, Any]:
        """활성 구독 없음 → ``default_free_code`` 로 자동 가입."""
        sub = await self.get_active_subscription(db, user_id)
        if sub is not None:
            plan = await db.scalar(
                select(self.Plan).where(self.Plan.id == sub.plan_id)
            )
            if plan is not None:
                return sub, plan

        free = await self.get_plan_by_code(db, self.default_free_code)
        if free is None:
            raise RuntimeError(
                f"기본 plan '{self.default_free_code}' 가 시드되지 않음 — alembic 적용 필요"
            )
        sub = self.Subscription(
            id=str(uuid.uuid4()),
            user_id=user_id,
            plan_id=free.id,
            status="active",
        )
        db.add(sub)
        await db.flush()
        return sub, free

    async def switch_subscription(
        self, db: AsyncSession, user_id: str, new_plan_code: str
    ) -> tuple[Any, Any, str | None]:
        """plan 전환 — 기존 ``active`` row 를 ``cancelled`` 로 마킹.

        Returns: (신규 active 구독, 신규 plan, 이전 plan_code | None).
        동일 plan 재구독은 변경 없음 (no-op).
        """
        new_plan = await self.get_plan_by_code(db, new_plan_code)
        if new_plan is None or not new_plan.is_active:
            raise ValueError(f"unknown or inactive plan: {new_plan_code}")

        previous_code: str | None = None
        existing = await self.get_active_subscription(db, user_id)
        if existing is not None:
            prev_plan = await db.scalar(
                select(self.Plan).where(self.Plan.id == existing.plan_id)
            )
            previous_code = prev_plan.code if prev_plan else None
            if previous_code == new_plan_code:
                return existing, new_plan, previous_code
            existing.status = "cancelled"
            existing.cancelled_at = datetime.now(timezone.utc)
            await db.flush()

        sub = self.Subscription(
            id=str(uuid.uuid4()),
            user_id=user_id,
            plan_id=new_plan.id,
            status="active",
        )
        db.add(sub)
        await db.flush()
        return sub, new_plan, previous_code

    # ── Monthly Usage ──────────────────────────────────────────────

    async def get_or_create_monthly_usage(
        self, db: AsyncSession, user_id: str, *, year_month: str | None = None
    ):
        ym = year_month or current_year_month()
        row = await db.scalar(
            select(self.MonthlyUsage)
            .where(self.MonthlyUsage.user_id == user_id)
            .where(self.MonthlyUsage.year_month == ym)
        )
        if row is not None:
            return row
        row = self.MonthlyUsage(
            id=str(uuid.uuid4()),
            user_id=user_id,
            year_month=ym,
            calls_count=0,
            tokens_total=0,
            cost_usd=0,
        )
        db.add(row)
        await db.flush()
        return row

    # ── 헬퍼 ────────────────────────────────────────────────────────

    @staticmethod
    def parse_allowed_models(allowed_models: str) -> list[str]:
        return parse_allowed_models(allowed_models)

    @staticmethod
    def usage_snapshot_dict(monthly, plan) -> dict[str, Any]:
        return usage_snapshot_dict(monthly, plan)


__all__ = ["BillingService"]
