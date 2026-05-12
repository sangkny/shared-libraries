"""shared-libraries/saas/stripe_service — Stripe Checkout + Webhook 어댑터.

설계 결정:
    - **환경변수 토글** — ``STRIPE_ENABLED=0`` (기본) 일 때 모든 라우트는 503.
      ``stripe`` python SDK 는 lazy import — 미설치 환경에서도 disabled 모드 안전.
    - **DI 기반** — ``StripeService`` 는 ``BillingService`` + 2개 sidecar ORM 클래스를
      받아 동작. ADK/CoOps 서비스별 독립 인스턴스 생성.
    - **이벤트 → BillingService 위임** — Stripe webhook 의 결정적 처리 (서명 검증
      + 페이로드 파싱) 만 본 모듈이 담당. 실제 plan 전환은 ``BillingService.switch_subscription``.
    - **테스트 친화** — ``parse_event`` 가 dict 를 받도록 분리. 서명 검증은 옵션
      (live 모드만). 단위 테스트는 dict payload 직접 주입.

지원 이벤트 (1라운드):
    - ``checkout.session.completed`` — 구독 시작
    - ``customer.subscription.updated`` — 상태/주기 갱신
    - ``customer.subscription.deleted`` — 구독 종료

미지원 (백로그):
    - invoice.* (인보이스 다운로드), customer.* (포털), refund, coupon
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .service import BillingService

log = logging.getLogger("saas.stripe")


def _emit_stripe_webhook(
    service_name: str | None, event_type: str, action: str
) -> None:
    """Prometheus ``saas_stripe_webhook_events_total`` (best-effort)."""
    if not service_name:
        return
    try:
        from observability import inc_saas_stripe_webhook

        inc_saas_stripe_webhook(
            service=service_name, event_type=event_type, action=action
        )
    except Exception:
        pass


class StripeDisabled(Exception):
    """``STRIPE_ENABLED=0`` 또는 키 미설정 — 라우트는 503 반환."""


class StripeSignatureError(Exception):
    """Webhook ``Stripe-Signature`` 헤더 검증 실패 — 라우트는 400 반환."""


# 지원 이벤트 (1라운드)
SUPPORTED_EVENTS: frozenset[str] = frozenset({
    "checkout.session.completed",
    "customer.subscription.updated",
    "customer.subscription.deleted",
})


@dataclass(frozen=True)
class StripeConfig:
    """환경변수 기반 Stripe 구성.

    ``STRIPE_ENABLED`` 가 "1" 이고 ``STRIPE_SECRET_KEY`` 가 있을 때만 enabled.
    """

    enabled: bool
    secret_key: str | None = None
    webhook_secret: str | None = None
    public_key: str | None = None
    default_success_url: str = "https://example.com/billing/success"
    default_cancel_url: str = "https://example.com/billing/cancel"
    skip_signature_verification: bool = False
    """테스트 전용 — webhook 서명 검증 건너뜀. 운영에서 절대 사용 금지."""

    @classmethod
    def from_env(cls, prefix: str = "") -> "StripeConfig":
        """``{PREFIX}STRIPE_*`` 환경변수에서 로드 (prefix 예: ``"ADK_"``).

        ``STRIPE_ENABLED`` 가 "0"/미설정 → ``enabled=False``.
        """
        def _g(name: str, default: str | None = None) -> str | None:
            return os.getenv(f"{prefix}{name}") or os.getenv(name) or default

        enabled = (_g("STRIPE_ENABLED", "0") or "0").lower() in {"1", "true", "yes"}
        secret = _g("STRIPE_SECRET_KEY")
        return cls(
            enabled=enabled and bool(secret),
            secret_key=secret,
            webhook_secret=_g("STRIPE_WEBHOOK_SECRET"),
            public_key=_g("STRIPE_PUBLIC_KEY"),
            default_success_url=_g(
                "STRIPE_SUCCESS_URL", "https://example.com/billing/success"
            ) or "https://example.com/billing/success",
            default_cancel_url=_g(
                "STRIPE_CANCEL_URL", "https://example.com/billing/cancel"
            ) or "https://example.com/billing/cancel",
            skip_signature_verification=(
                _g("STRIPE_SKIP_SIG_VERIFY", "0") or "0"
            ).lower() in {"1", "true", "yes"},
        )


class StripeService:
    """Stripe Checkout + Webhook 핸들러.

    Args:
        config: 환경변수에서 빌드한 ``StripeConfig``.
        billing: 같은 서비스의 ``BillingService`` 인스턴스.
        plan_mapping_cls / stripe_subscription_cls: ``make_stripe_models`` 가
            반환한 2 sidecar ORM 클래스.
    """

    def __init__(
        self,
        *,
        config: StripeConfig,
        billing: BillingService,
        plan_mapping_cls,
        stripe_subscription_cls,
    ) -> None:
        self.config = config
        self.billing = billing
        self.PlanMapping = plan_mapping_cls
        self.StripeSub = stripe_subscription_cls

    # ── 가용성 ───────────────────────────────────────────────────────

    def is_enabled(self) -> bool:
        return self.config.enabled

    def _require_enabled(self) -> None:
        if not self.is_enabled():
            raise StripeDisabled(
                "Stripe 가 비활성 상태 (STRIPE_ENABLED=0 또는 STRIPE_SECRET_KEY 미설정)"
            )

    # ── Checkout ────────────────────────────────────────────────────

    async def create_checkout_session(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        plan_code: str,
        success_url: str | None = None,
        cancel_url: str | None = None,
        client_reference_id: str | None = None,
    ) -> dict[str, Any]:
        """plan_code 에 해당하는 Stripe Checkout Session 생성.

        Returns dict with ``id`` (session id) and ``url`` (redirect target).
        """
        self._require_enabled()

        plan = await self.billing.get_plan_by_code(db, plan_code)
        if plan is None or not getattr(plan, "is_active", False):
            raise ValueError(f"unknown or inactive plan: {plan_code}")

        mapping = await db.scalar(
            select(self.PlanMapping).where(self.PlanMapping.plan_id == plan.id)
        )
        if mapping is None:
            raise ValueError(
                f"plan '{plan_code}' has no Stripe price mapping — "
                "POST /billing/admin/stripe/plan-mapping 으로 stripe_price_id 등록 필요"
            )

        import stripe as _stripe
        _stripe.api_key = self.config.secret_key

        session = _stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": mapping.stripe_price_id, "quantity": 1}],
            success_url=success_url or self.config.default_success_url,
            cancel_url=cancel_url or self.config.default_cancel_url,
            client_reference_id=client_reference_id or user_id,
            metadata={
                "user_id": user_id,
                "plan_code": plan_code,
            },
        )
        log.info(
            "Stripe checkout session: user=%s plan=%s session=%s",
            user_id, plan_code, getattr(session, "id", "?"),
        )
        return {"id": session.id, "url": session.url}

    # ── Webhook 파싱 ────────────────────────────────────────────────

    def parse_event(
        self, raw_body: bytes, signature_header: str | None = None
    ) -> dict[str, Any]:
        """Webhook 페이로드 파싱 + 서명 검증.

        ``config.skip_signature_verification=True`` (테스트) 면 단순 JSON 파싱.
        라이브 모드에서는 ``stripe.Webhook.construct_event`` 로 검증.
        """
        if self.config.skip_signature_verification:
            try:
                return json.loads(raw_body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                raise StripeSignatureError(f"invalid JSON: {e}") from e

        if not self.config.webhook_secret:
            raise StripeSignatureError("STRIPE_WEBHOOK_SECRET 미설정")
        if not signature_header:
            raise StripeSignatureError("Stripe-Signature 헤더 없음")

        import stripe as _stripe
        try:
            event = _stripe.Webhook.construct_event(
                payload=raw_body,
                sig_header=signature_header,
                secret=self.config.webhook_secret,
            )
        except Exception as e:  # SignatureVerificationError / ValueError
            raise StripeSignatureError(str(e)) from e
        return event if isinstance(event, dict) else event.to_dict_recursive()

    # ── 이벤트 처리 ────────────────────────────────────────────────

    async def handle_event(
        self, db: AsyncSession, event: dict[str, Any]
    ) -> dict[str, Any]:
        """Stripe 이벤트를 받아 적절한 핸들러로 분배.

        반환 dict 는 라우트가 그대로 JSON 응답으로 사용 (received, type, action).
        """
        etype = str(event.get("type", ""))
        if etype not in SUPPORTED_EVENTS:
            log.info("Stripe 이벤트 미지원 — type=%s (건너뜀)", etype)
            return {"received": True, "type": etype, "action": "ignored"}

        obj = (event.get("data") or {}).get("object") or {}
        if etype == "checkout.session.completed":
            action = await self._handle_checkout_completed(db, obj)
        elif etype == "customer.subscription.updated":
            action = await self._handle_subscription_updated(db, obj)
        elif etype == "customer.subscription.deleted":
            action = await self._handle_subscription_deleted(db, obj)
        else:  # pragma: no cover
            action = "ignored"

        _emit_stripe_webhook(
            getattr(self.billing, "service_name", None), etype, action
        )
        return {"received": True, "type": etype, "action": action}

    async def _handle_checkout_completed(
        self, db: AsyncSession, obj: dict[str, Any]
    ) -> str:
        """``checkout.session.completed`` — Plan 전환 + sidecar 생성.

        필수 필드:
            metadata.user_id, metadata.plan_code, customer, subscription, mode='subscription'
        """
        if obj.get("mode") != "subscription":
            return "ignored_mode"
        meta = obj.get("metadata") or {}
        user_id = str(meta.get("user_id") or obj.get("client_reference_id") or "")
        plan_code = str(meta.get("plan_code") or "")
        customer_id = str(obj.get("customer") or "")
        sub_id_stripe = str(obj.get("subscription") or "")

        if not (user_id and plan_code and customer_id and sub_id_stripe):
            log.warning(
                "checkout.session.completed missing fields: user=%s plan=%s cust=%s sub=%s",
                user_id, plan_code, customer_id, sub_id_stripe,
            )
            return "missing_fields"

        sub, _plan, _prev = await self.billing.switch_subscription(
            db, user_id=user_id, new_plan_code=plan_code
        )

        # sidecar upsert
        side = await db.scalar(
            select(self.StripeSub).where(
                self.StripeSub.subscription_id == sub.id
            )
        )
        if side is None:
            side = self.StripeSub(
                id=str(uuid.uuid4()),
                subscription_id=sub.id,
                stripe_customer_id=customer_id,
                stripe_subscription_id=sub_id_stripe,
                stripe_status="active",
            )
            db.add(side)
        else:
            side.stripe_customer_id = customer_id
            side.stripe_subscription_id = sub_id_stripe
            side.stripe_status = "active"
        await db.flush()
        log.info(
            "checkout.completed → switched: user=%s plan=%s sub=%s",
            user_id, plan_code, sub_id_stripe,
        )
        return "switched"

    async def _handle_subscription_updated(
        self, db: AsyncSession, obj: dict[str, Any]
    ) -> str:
        """``customer.subscription.updated`` — status / period_end / cancel_at 갱신."""
        sub_id_stripe = str(obj.get("id") or "")
        if not sub_id_stripe:
            return "missing_id"
        side = await db.scalar(
            select(self.StripeSub).where(
                self.StripeSub.stripe_subscription_id == sub_id_stripe
            )
        )
        if side is None:
            log.warning("subscription.updated for unknown sub=%s", sub_id_stripe)
            return "unknown"

        side.stripe_status = str(obj.get("status") or side.stripe_status)
        side.cancel_at_period_end = bool(obj.get("cancel_at_period_end", False))
        cpe = obj.get("current_period_end")
        if isinstance(cpe, (int, float)):
            side.current_period_end = datetime.fromtimestamp(
                int(cpe), tz=timezone.utc
            )
        await db.flush()
        return "updated"

    async def _handle_subscription_deleted(
        self, db: AsyncSession, obj: dict[str, Any]
    ) -> str:
        """``customer.subscription.deleted`` — 구독 종료 → free 로 전환."""
        sub_id_stripe = str(obj.get("id") or "")
        if not sub_id_stripe:
            return "missing_id"
        side = await db.scalar(
            select(self.StripeSub).where(
                self.StripeSub.stripe_subscription_id == sub_id_stripe
            )
        )
        if side is None:
            return "unknown"

        # 활성 sub 마킹 cancelled, free 로 전환 (free seed 필요)
        sub = await db.scalar(
            select(self.billing.Subscription).where(
                self.billing.Subscription.id == side.subscription_id
            )
        )
        if sub is not None and sub.status == "active":
            try:
                await self.billing.switch_subscription(
                    db,
                    user_id=sub.user_id,
                    new_plan_code=self.billing.default_free_code,
                )
            except ValueError:
                log.warning(
                    "subscription.deleted but free plan '%s' not seeded",
                    self.billing.default_free_code,
                )

        side.stripe_status = "canceled"
        side.cancel_at_period_end = False
        await db.flush()
        return "canceled"

    # ── Plan ↔ Stripe Price 매핑 (admin) ─────────────────────────

    async def set_plan_mapping(
        self, db: AsyncSession, *, plan_code: str, stripe_price_id: str
    ) -> Any:
        """Admin 라우트용 — plan 에 stripe_price_id 등록 (upsert)."""
        plan = await self.billing.get_plan_by_code(db, plan_code)
        if plan is None:
            raise ValueError(f"unknown plan: {plan_code}")
        mapping = await db.scalar(
            select(self.PlanMapping).where(self.PlanMapping.plan_id == plan.id)
        )
        if mapping is None:
            mapping = self.PlanMapping(
                id=str(uuid.uuid4()),
                plan_id=plan.id,
                stripe_price_id=stripe_price_id,
            )
            db.add(mapping)
        else:
            mapping.stripe_price_id = stripe_price_id
        await db.flush()
        return mapping

    async def get_plan_mapping(
        self, db: AsyncSession, *, plan_code: str
    ) -> Any | None:
        plan = await self.billing.get_plan_by_code(db, plan_code)
        if plan is None:
            return None
        return await db.scalar(
            select(self.PlanMapping).where(self.PlanMapping.plan_id == plan.id)
        )


__all__ = [
    "StripeConfig",
    "StripeService",
    "StripeDisabled",
    "StripeSignatureError",
    "SUPPORTED_EVENTS",
]
