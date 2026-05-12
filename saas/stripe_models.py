"""shared-libraries/saas/stripe_models — Stripe sidecar ORM factory.

기존 ``billing_plans`` / ``billing_subscriptions`` 를 수정하지 않고 1:1 sidecar
테이블로 Stripe 메타데이터를 분리한다.

- ``stripe_plan_mappings`` — Plan ↔ Stripe Price 매핑 (env 별 가격 ID 분리 가능)
- ``stripe_subscriptions`` — Subscription ↔ Stripe Customer/Subscription/PeriodEnd

ADK (prefix="") / CoOps (prefix="coops_") 등 멀티 서비스가 ``make_stripe_models``
factory 로 자기 ``Base`` + prefix 를 주입해 격리.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column


def make_stripe_models(Base, table_prefix: str = ""):
    """2개 sidecar ORM 클래스 (StripePlanMapping, StripeSubscription) 생성.

    Args:
        Base: 서비스 ``DeclarativeBase``. ``make_billing_models`` 와 동일 Base.
        table_prefix: 테이블명 prefix (``""`` for ADK, ``"coops_"`` for CoOps 등).

    Returns:
        ``(PlanMappingCls, StripeSubscriptionCls)`` 튜플.
    """
    p = table_prefix or ""
    plans_tbl = f"{p}billing_plans"
    subs_tbl = f"{p}billing_subscriptions"
    plan_map_tbl = f"{p}stripe_plan_mappings"
    sub_side_tbl = f"{p}stripe_subscriptions"

    class StripePlanMapping(Base):
        """Plan ↔ Stripe Price 매핑 (1:1 sidecar).

        prod/test 환경에서 같은 plan_code 에 다른 ``stripe_price_id`` 를 매핑할 수
        있도록 별 테이블로 분리. plan 삭제 시 ``CASCADE``.
        """

        __tablename__ = plan_map_tbl

        id: Mapped[str] = mapped_column(
            String(36), primary_key=True, default=lambda: str(uuid.uuid4())
        )
        plan_id: Mapped[str] = mapped_column(
            String(36),
            ForeignKey(f"{plans_tbl}.id", ondelete="CASCADE"),
            nullable=False, unique=True, index=True,
        )
        stripe_price_id: Mapped[str] = mapped_column(
            String(128), nullable=False, unique=True, index=True,
            comment="Stripe Price object id (e.g. price_1Abc...)",
        )

        created_at: Mapped[datetime] = mapped_column(
            DateTime(timezone=True), server_default=func.now(), nullable=False,
        )
        updated_at: Mapped[datetime] = mapped_column(
            DateTime(timezone=True),
            server_default=func.now(), onupdate=func.now(), nullable=False,
        )

    class StripeSubscription(Base):
        """Subscription ↔ Stripe Customer/Subscription (1:1 sidecar)."""

        __tablename__ = sub_side_tbl

        id: Mapped[str] = mapped_column(
            String(36), primary_key=True, default=lambda: str(uuid.uuid4())
        )
        subscription_id: Mapped[str] = mapped_column(
            String(36),
            ForeignKey(f"{subs_tbl}.id", ondelete="CASCADE"),
            nullable=False, unique=True, index=True,
        )
        stripe_customer_id: Mapped[str] = mapped_column(
            String(128), nullable=False, index=True,
        )
        stripe_subscription_id: Mapped[str] = mapped_column(
            String(128), nullable=False, unique=True, index=True,
        )
        stripe_status: Mapped[str] = mapped_column(
            String(32), nullable=False, default="incomplete",
            comment="Stripe subscription status (trialing/active/past_due/canceled/...)",
        )
        current_period_end: Mapped[datetime | None] = mapped_column(
            DateTime(timezone=True), nullable=True,
        )
        cancel_at_period_end: Mapped[bool] = mapped_column(
            Boolean, nullable=False, default=False,
        )

        # B-7 Round 2 — invoice / refund 메타데이터
        last_paid_at: Mapped[datetime | None] = mapped_column(
            DateTime(timezone=True), nullable=True,
            comment="가장 최근 invoice.paid 시각",
        )
        last_paid_amount_cents: Mapped[int | None] = mapped_column(
            BigInteger, nullable=True,
            comment="가장 최근 invoice.paid 금액 (cents, Stripe ``amount_paid``)",
        )
        last_failure_at: Mapped[datetime | None] = mapped_column(
            DateTime(timezone=True), nullable=True,
            comment="가장 최근 invoice.payment_failed 시각",
        )

        created_at: Mapped[datetime] = mapped_column(
            DateTime(timezone=True), server_default=func.now(), nullable=False,
        )
        updated_at: Mapped[datetime] = mapped_column(
            DateTime(timezone=True),
            server_default=func.now(), onupdate=func.now(), nullable=False,
        )

    return StripePlanMapping, StripeSubscription


__all__ = ["make_stripe_models"]
