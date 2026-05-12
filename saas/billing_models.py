"""shared-libraries/saas/billing_models — ORM factory for multi-service SaaS billing.

각 서비스 (ADK, CoOps, 향후 MEDI) 가 자기 ``declarative Base`` 와 ``table_prefix``
를 주입해 4개 ORM 클래스를 생성. 같은 DB 안에서도 테이블 충돌 회피.

사용 예::

    # ADK (table_prefix="" — 기존 ``billing_plans`` 그대로 유지)
    from database import Base
    from shared_libraries.saas import make_billing_models

    BillingPlan, BillingSubscription, BillingUsageRecord, BillingMonthlyUserUsage = \
        make_billing_models(Base, table_prefix="")

    # CoOps (table_prefix="coops_" — coops_billing_plans 등 신규)
    BillingPlan, BillingSubscription, BillingUsageRecord, BillingMonthlyUserUsage = \
        make_billing_models(Base, table_prefix="coops_")
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column


def make_billing_models(Base, table_prefix: str = ""):
    """4개 ORM 클래스 (Plan, Subscription, UsageRecord, MonthlyUserUsage) 생성.

    Args:
        Base: 서비스의 ``DeclarativeBase`` 클래스.
        table_prefix: 테이블명 prefix (예: ``""``, ``"coops_"``). 끝에 자동
            ``_`` 안 붙음 — 호출자가 명시.

    Returns:
        ``(PlanCls, SubscriptionCls, UsageRecordCls, MonthlyUsageCls)`` 튜플.
    """
    p = table_prefix or ""
    plans_tbl = f"{p}billing_plans"
    subs_tbl = f"{p}billing_subscriptions"
    usage_tbl = f"{p}billing_usage_records"
    monthly_tbl = f"{p}billing_monthly_user_usage"
    uq_monthly = f"uq_{p}billing_monthly_user_year_month"

    class BillingPlan(Base):
        """SaaS Plan 카탈로그.

        ``monthly_call_quota=None`` → 무제한. ``allowed_models`` 는 CSV
        (``"FAST"``, ``"FAST,HEAVY"``, ``"FAST,HEAVY,CONSENSUS"``).
        """

        __tablename__ = plans_tbl

        id: Mapped[str] = mapped_column(
            String(36), primary_key=True, default=lambda: str(uuid.uuid4())
        )
        code: Mapped[str] = mapped_column(
            String(32), nullable=False, unique=True, index=True
        )
        name: Mapped[str] = mapped_column(String(64), nullable=False)
        price_usd_per_month: Mapped[float] = mapped_column(
            Numeric(10, 2), nullable=False, default=0
        )
        monthly_call_quota: Mapped[int | None] = mapped_column(Integer, nullable=True)
        allowed_models: Mapped[str] = mapped_column(
            String(128), nullable=False, default="FAST"
        )
        description: Mapped[str | None] = mapped_column(Text, nullable=True)
        is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

        created_at: Mapped[datetime] = mapped_column(
            DateTime(timezone=True), server_default=func.now(), nullable=False
        )
        updated_at: Mapped[datetime] = mapped_column(
            DateTime(timezone=True),
            server_default=func.now(),
            onupdate=func.now(),
            nullable=False,
        )

    class BillingSubscription(Base):
        """user_id → Plan 연결 (활성 단일 + 감사 추적)."""

        __tablename__ = subs_tbl

        id: Mapped[str] = mapped_column(
            String(36), primary_key=True, default=lambda: str(uuid.uuid4())
        )
        user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
        plan_id: Mapped[str] = mapped_column(
            String(36),
            ForeignKey(f"{plans_tbl}.id", ondelete="RESTRICT"),
            nullable=False,
        )
        status: Mapped[str] = mapped_column(
            String(16), nullable=False, default="active", index=True
        )

        started_at: Mapped[datetime] = mapped_column(
            DateTime(timezone=True), server_default=func.now(), nullable=False
        )
        current_period_end: Mapped[datetime | None] = mapped_column(
            DateTime(timezone=True), nullable=True
        )
        cancelled_at: Mapped[datetime | None] = mapped_column(
            DateTime(timezone=True), nullable=True
        )

        created_at: Mapped[datetime] = mapped_column(
            DateTime(timezone=True), server_default=func.now(), nullable=False
        )

    class BillingUsageRecord(Base):
        """호출 단위 사용 기록 (감사·분석용).

        ``plan_code`` 는 호출 시점의 plan 스냅샷 (사후 plan 변경에도 청구 정합성).
        """

        __tablename__ = usage_tbl

        id: Mapped[str] = mapped_column(
            String(36), primary_key=True, default=lambda: str(uuid.uuid4())
        )
        user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
        action: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
        plan_code: Mapped[str] = mapped_column(String(32), nullable=False)

        tokens_estimated: Mapped[int] = mapped_column(
            Integer, default=0, nullable=False
        )
        model_used: Mapped[str | None] = mapped_column(String(128), nullable=True)
        success: Mapped[bool] = mapped_column(
            Boolean, default=True, nullable=False
        )
        latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

        created_at: Mapped[datetime] = mapped_column(
            DateTime(timezone=True),
            server_default=func.now(),
            nullable=False,
            index=True,
        )

    class BillingMonthlyUserUsage(Base):
        """월별 사용자별 집계 (성능 위 — 매 호출 COUNT 회피)."""

        __tablename__ = monthly_tbl

        id: Mapped[str] = mapped_column(
            String(36), primary_key=True, default=lambda: str(uuid.uuid4())
        )
        user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
        year_month: Mapped[str] = mapped_column(String(7), nullable=False, index=True)
        calls_count: Mapped[int] = mapped_column(
            Integer, default=0, nullable=False
        )
        tokens_total: Mapped[int] = mapped_column(
            Integer, default=0, nullable=False
        )
        cost_usd: Mapped[float] = mapped_column(
            Numeric(18, 8), default=0, nullable=False
        )

        last_updated: Mapped[datetime] = mapped_column(
            DateTime(timezone=True),
            server_default=func.now(),
            onupdate=func.now(),
            nullable=False,
        )

        __table_args__ = (
            UniqueConstraint("user_id", "year_month", name=uq_monthly),
        )

    return BillingPlan, BillingSubscription, BillingUsageRecord, BillingMonthlyUserUsage


__all__ = ["make_billing_models"]
