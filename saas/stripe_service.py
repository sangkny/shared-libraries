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

지원 이벤트:
    Round 1
    - ``checkout.session.completed`` — 구독 시작
    - ``customer.subscription.updated`` — 상태/주기 갱신
    - ``customer.subscription.deleted`` — 구독 종료

    Round 2 (E-R2 이후)
    - ``invoice.paid`` — 결제 성공 → ``saas_stripe_revenue_usd_total`` 누적
    - ``invoice.payment_failed`` — 결제 실패 → 알림용 카운터
    - ``charge.refunded`` — 환불 → 매출 차감 카운터

Customer Portal (B-7 Round 2):
    Stripe 가 제공하는 self-serve 포털로 결제수단 변경/취소/인보이스 조회 가능.
    ``create_portal_session`` 가 redirect URL 을 반환.

Round 3 (B-7 R3, 2026-05-14):
    - **Metered usage** — ``submit_metered_usage`` 가 Stripe SubscriptionItem 에
      UsageRecord 전송 (metered price 가 붙은 항목 자동 탐색).
    - **dispute.*** — ``charge.dispute.{created,closed,funds_withdrawn,funds_reinstated}``
      수신 시 메트릭 + 선택적 Slack 알림.
    - **멀티 통화** — ``invoice.paid`` / ``charge.refunded`` 시 ``saas_stripe_revenue_minor_total``
      누적 + (환경변수 FX) 기반 USD 근사치를 ``saas_stripe_revenue_usd_total`` 에 반영.
    - **Slack** — ``STRIPE_SLACK_WEBHOOK_URL`` 설정 시 결제 실패/환불/분쟁 생성 알림.
    - **쿠폰/프로모션** — Checkout ``allow_promotion_codes`` 또는 ``promotion_code`` /
      ``coupon`` Stripe id 전달.
    - **Customer Portal** — ``flow=invoice_history`` 등 ``flow_data`` 로 인보이스
      화면 직행 (모바일 return_url 과 함께 사용).
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass
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


def _emit_stripe_revenue_minor(
    service_name: str | None,
    amount_minor: float,
    currency: str,
    direction: str = "paid",
) -> None:
    """Prometheus ``saas_stripe_revenue_minor_total`` (best-effort)."""
    if not service_name:
        return
    try:
        from observability import inc_saas_stripe_revenue_minor

        inc_saas_stripe_revenue_minor(
            service=service_name,
            amount_minor=float(amount_minor),
            currency=currency,
            direction=direction,
        )
    except Exception:
        pass


def _emit_stripe_dispute(
    service_name: str | None, event_type: str, dispute_status: str
) -> None:
    if not service_name:
        return
    try:
        from observability import inc_saas_stripe_dispute

        inc_saas_stripe_dispute(
            service=service_name,
            event_type=event_type,
            dispute_status=dispute_status,
        )
    except Exception:
        pass


def _emit_stripe_revenue(
    service_name: str | None, amount_usd: float, direction: str = "paid"
) -> None:
    """Prometheus ``saas_stripe_revenue_usd_total`` (best-effort).

    ``direction`` ∈ {"paid", "refunded"}. paid 는 +, refunded 는 + (label 로 구분).
    Net revenue 는 Grafana 에서 ``sum(paid) - sum(refunded)`` 로 계산.
    """
    if not service_name:
        return
    try:
        from observability import inc_saas_stripe_revenue

        inc_saas_stripe_revenue(
            service=service_name, amount_usd=float(amount_usd), direction=direction
        )
    except Exception:
        pass


# Stripe zero-decimal 통화 — amount_* 가 "원/엔" 등 major 단위 정수
_ZERO_DECIMAL: frozenset[str] = frozenset({
    "bif", "clp", "djf", "gnf", "jpy", "kmf", "krw", "mga", "pyg", "rwf",
    "ugx", "vnd", "vuv", "xaf", "xof", "xpf",
})


class StripeDisabled(Exception):
    """``STRIPE_ENABLED=0`` 또는 키 미설정 — 라우트는 503 반환."""


class StripeSignatureError(Exception):
    """Webhook ``Stripe-Signature`` 헤더 검증 실패 — 라우트는 400 반환."""


# 지원 이벤트 (Round 1 + Round 2 + Round 3)
SUPPORTED_EVENTS: frozenset[str] = frozenset({
    "checkout.session.completed",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    # B-7 Round 2 추가
    "invoice.paid",
    "invoice.payment_failed",
    "charge.refunded",
    # B-7 Round 3 — disputes
    "charge.dispute.created",
    "charge.dispute.closed",
    "charge.dispute.funds_withdrawn",
    "charge.dispute.funds_reinstated",
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

    # B-7 Round 3 — Slack + FX (major→USD 근사, Stripe minor 단위는 핸들러에서 처리)
    slack_webhook_url: str | None = None
    """Incoming Webhook URL — 결제 실패/환불/분쟁 생성 시 JSON ``{"text":...}`` POST."""

    fx_eur_usd: float = 1.08
    """1 EUR major = ? USD (EUR 금액이 cents 일 때는 amount/100 * 본 값)."""

    fx_jpy_usd: float = 0.0067
    """1 JPY = ? USD (Stripe JPY 는 zero-decimal, amount 가 엔 단위)."""

    fx_krw_usd: float = 0.00074
    """1 KRW = ? USD (Stripe KRW 는 zero-decimal)."""

    @classmethod
    def from_env(cls, prefix: str = "") -> "StripeConfig":
        """``{PREFIX}STRIPE_*`` 환경변수에서 로드 (prefix 예: ``"ADK_"``).

        ``STRIPE_ENABLED`` 가 "0"/미설정 → ``enabled=False``.
        """
        def _g(name: str, default: str | None = None) -> str | None:
            return os.getenv(f"{prefix}{name}") or os.getenv(name) or default

        def _f(name: str, default: str) -> float:
            raw = _g(name, default)
            try:
                return float(raw or default)
            except (TypeError, ValueError):
                return float(default)

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
            slack_webhook_url=(_g("STRIPE_SLACK_WEBHOOK_URL", "") or None),
            fx_eur_usd=_f("STRIPE_FX_EUR_USD", "1.08"),
            fx_jpy_usd=_f("STRIPE_FX_JPY_USD", "0.0067"),
            fx_krw_usd=_f("STRIPE_FX_KRW_USD", "0.00074"),
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

    def _stripe_amount_to_usd_approx(self, amount_minor: int, currency: str) -> float:
        """invoice/charge 금액을 USD 로 근사 (FX 는 ``StripeConfig`` 환경변수).

        USD/EUR 는 cents, JPY/KRW 는 zero-decimal (엔/원 정수). 그 외 통화는 0.
        """
        cur = (currency or "usd").lower()
        amt = int(amount_minor)
        if cur == "usd":
            return amt / 100.0
        if cur == "eur":
            return (amt / 100.0) * float(self.config.fx_eur_usd)
        if cur in _ZERO_DECIMAL:
            if cur == "jpy":
                return float(amt) * float(self.config.fx_jpy_usd)
            if cur == "krw":
                return float(amt) * float(self.config.fx_krw_usd)
        return 0.0

    async def _slack_notify(self, text: str) -> None:
        """Incoming Webhook — best-effort, 실패해도 웹훅 처리는 계속."""
        url = self.config.slack_webhook_url
        if not url or not text.strip():
            return
        try:
            import httpx

            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(url, json={"text": text[:3500]})
        except Exception:
            log.exception("Slack webhook POST 실패 (무시)")

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
        allow_promotion_codes: bool = False,
        promotion_code: str | None = None,
        coupon_id: str | None = None,
    ) -> dict[str, Any]:
        """plan_code 에 해당하는 Stripe Checkout Session 생성.

        B-7 R3: ``allow_promotion_codes`` / ``promotion_code`` (``promo_...`` id) /
        ``coupon_id`` (``coupon_...`` id) 는 Stripe Checkout 옵션과 직접 대응.
        ``promotion_code`` 와 ``coupon_id`` 는 동시에 넣지 말 것 (Stripe 제약).

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

        params: dict[str, Any] = {
            "mode": "subscription",
            "line_items": [{"price": mapping.stripe_price_id, "quantity": 1}],
            "success_url": success_url or self.config.default_success_url,
            "cancel_url": cancel_url or self.config.default_cancel_url,
            "client_reference_id": client_reference_id or user_id,
            "metadata": {
                "user_id": user_id,
                "plan_code": plan_code,
            },
        }
        if allow_promotion_codes:
            params["allow_promotion_codes"] = True
        if promotion_code:
            params["discounts"] = [{"promotion_code": promotion_code}]
        elif coupon_id:
            params["discounts"] = [{"coupon": coupon_id}]

        session = _stripe.checkout.Session.create(**params)
        log.info(
            "Stripe checkout session: user=%s plan=%s session=%s promo=%s",
            user_id, plan_code, getattr(session, "id", "?"),
            bool(allow_promotion_codes or promotion_code or coupon_id),
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
        elif etype == "invoice.paid":
            action = await self._handle_invoice_paid(db, obj)
        elif etype == "invoice.payment_failed":
            action = await self._handle_invoice_failed(db, obj)
        elif etype == "charge.refunded":
            action = await self._handle_charge_refunded(db, obj)
        elif etype.startswith("charge.dispute."):
            action = await self._handle_dispute(db, etype, obj)
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

    # ── Round 2: invoice / refund 이벤트 ─────────────────────────

    async def _handle_invoice_paid(
        self, db: AsyncSession, obj: dict[str, Any]
    ) -> str:
        """``invoice.paid`` — sidecar 갱신 + minor 통화 메트릭 + FX 기반 USD 근사."""
        sub_id_stripe = str(obj.get("subscription") or "")
        amount_minor = int(obj.get("amount_paid") or 0)
        currency = str(obj.get("currency") or "usd").lower()
        if amount_minor <= 0:
            return "no_amount"

        svc = getattr(self.billing, "service_name", None)
        _emit_stripe_revenue_minor(
            svc, float(amount_minor), currency, direction="paid"
        )
        usd = self._stripe_amount_to_usd_approx(amount_minor, currency)
        if usd > 0:
            _emit_stripe_revenue(svc, usd, direction="paid")

        if sub_id_stripe:
            side = await db.scalar(
                select(self.StripeSub).where(
                    self.StripeSub.stripe_subscription_id == sub_id_stripe
                )
            )
            if side is not None:
                if hasattr(side, "last_paid_at"):
                    side.last_paid_at = datetime.now(timezone.utc)
                if hasattr(side, "last_paid_amount_cents"):
                    side.last_paid_amount_cents = amount_minor
                await db.flush()

        log.info(
            "invoice.paid sub=%s amount_minor=%d currency=%s usd_approx=%.4f",
            sub_id_stripe, amount_minor, currency, usd,
        )
        return "paid"

    async def _handle_invoice_failed(
        self, db: AsyncSession, obj: dict[str, Any]
    ) -> str:
        """``invoice.payment_failed`` — sidecar 메모 + Slack (설정 시)."""
        sub_id_stripe = str(obj.get("subscription") or "")
        inv_id = str(obj.get("id") or "")
        if sub_id_stripe:
            side = await db.scalar(
                select(self.StripeSub).where(
                    self.StripeSub.stripe_subscription_id == sub_id_stripe
                )
            )
            if side is not None and hasattr(side, "last_failure_at"):
                side.last_failure_at = datetime.now(timezone.utc)
                await db.flush()

        log.warning("invoice.payment_failed sub=%s inv=%s", sub_id_stripe, inv_id)
        await self._slack_notify(
            f"[Stripe] invoice.payment_failed\n"
            f"subscription={sub_id_stripe or '—'}\ninvoice={inv_id or '—'}\n"
            f"amount_due={obj.get('amount_due')} currency={obj.get('currency')}"
        )
        return "failed"

    async def _handle_charge_refunded(
        self, db: AsyncSession, obj: dict[str, Any]
    ) -> str:
        """``charge.refunded`` — minor + USD(환불) 메트릭 + Slack."""
        amount_minor = int(obj.get("amount_refunded") or 0)
        currency = str(obj.get("currency") or "usd").lower()
        if amount_minor <= 0:
            return "no_amount"

        svc = getattr(self.billing, "service_name", None)
        _emit_stripe_revenue_minor(
            svc, float(amount_minor), currency, direction="refunded"
        )
        usd = self._stripe_amount_to_usd_approx(amount_minor, currency)
        if usd > 0:
            _emit_stripe_revenue(svc, usd, direction="refunded")

        await self._slack_notify(
            f"[Stripe] charge.refunded\n"
            f"charge={obj.get('id')}\namount_refunded={amount_minor} {currency.upper()}\n"
            f"usd_approx={usd:.4f}"
        )
        log.info(
            "charge.refunded amount_minor=%d currency=%s usd_approx=%.4f",
            amount_minor, currency, usd,
        )
        return "refunded"

    async def _handle_dispute(
        self, db: AsyncSession, etype: str, obj: dict[str, Any]
    ) -> str:
        """``charge.dispute.*`` — 메트릭 + 생성 시 Slack (금전적 영향은 Stripe 가 처리)."""
        _ = db
        st = str(obj.get("status") or "unknown")
        disp_id = str(obj.get("id") or "?")
        ch = str(obj.get("charge") or "?")
        amt = obj.get("amount")
        cur = str(obj.get("currency") or "")

        svc = getattr(self.billing, "service_name", None)
        _emit_stripe_dispute(svc, etype, st)

        if etype == "charge.dispute.created":
            await self._slack_notify(
                f"[Stripe] charge.dispute.created\n"
                f"dispute={disp_id}\ncharge={ch}\nstatus={st}\n"
                f"amount={amt} {cur}"
            )
        log.info("Stripe dispute event type=%s id=%s status=%s", etype, disp_id, st)
        return f"dispute_{st[:24]}"

    # ── Round 2: Customer Portal ────────────────────────────────

    async def create_portal_session(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        return_url: str | None = None,
        flow: str | None = None,
    ) -> dict[str, Any]:
        """Stripe Customer Portal session 생성.

        B-7 R3: ``flow='invoice_history'`` 이면 ``flow_data`` 로 인보이스 목록 화면
        직행 (모바일에서 ``return_url=myapp://...`` 과 함께 사용). Stripe API 가
        미지원/거부하면 flow 없이 기본 포털로 폴백.

        Raises:
            ``StripeDisabled`` — toggle off.
            ``ValueError`` — sidecar 없음 (구독 이력 없음).
        """
        self._require_enabled()

        sub = await self.billing.get_active_subscription(db, user_id)
        if sub is None:
            raise ValueError("no_active_subscription")

        side = await db.scalar(
            select(self.StripeSub).where(self.StripeSub.subscription_id == sub.id)
        )
        if side is None or not getattr(side, "stripe_customer_id", None):
            raise ValueError("no_stripe_customer")

        import stripe as _stripe
        _stripe.api_key = self.config.secret_key

        base_kw: dict[str, Any] = {
            "customer": side.stripe_customer_id,
            "return_url": return_url or self.config.default_success_url,
        }
        session = None
        if flow == "invoice_history":
            try:
                session = _stripe.billing_portal.Session.create(
                    **base_kw,
                    flow_data={"type": "invoice_history"},
                )
            except Exception as exc:
                log.warning(
                    "portal flow_data invoice_history 미지원/실패 — 기본 포털로 폴백: %s",
                    exc,
                )
        if session is None:
            session = _stripe.billing_portal.Session.create(**base_kw)

        log.info(
            "Stripe portal session: user=%s customer=%s session=%s flow=%s",
            user_id, side.stripe_customer_id, getattr(session, "id", "?"), flow,
        )
        return {"id": session.id, "url": session.url}

    # ── Round 3: Metered usage ───────────────────────────────────

    async def submit_metered_usage(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        quantity: float,
        action: str = "increment",
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        """Stripe metered SubscriptionItem 에 UsageRecord 전송.

        활성 구독의 ``stripe_subscription_id`` 로 Subscription 을 조회한 뒤,
        ``recurring.usage_type == 'metered'`` 인 첫 ``items[].id`` 에 기록한다.
        metered 항목이 없으면 ``status=skipped_no_metered_item`` 반환 (에러 아님).

        ``action`` 은 Stripe UsageRecord API 의 ``increment`` | ``set`` (기본 increment).

        Raises:
            ``StripeDisabled``, ``ValueError`` (활성 구독/sidecar 없음).
        """
        self._require_enabled()
        if quantity < 0:
            raise ValueError("quantity must be non-negative")
        if action not in {"increment", "set"}:
            raise ValueError("action must be increment or set")

        sub = await self.billing.get_active_subscription(db, user_id)
        if sub is None:
            raise ValueError("no_active_subscription")
        side = await db.scalar(
            select(self.StripeSub).where(self.StripeSub.subscription_id == sub.id)
        )
        if side is None or not getattr(side, "stripe_subscription_id", None):
            raise ValueError("no_stripe_subscription")

        import stripe as _stripe
        _stripe.api_key = self.config.secret_key

        stripe_sub = _stripe.Subscription.retrieve(
            side.stripe_subscription_id,
            expand=["items.data.price"],
        )
        items = getattr(stripe_sub, "items", None)
        data = (getattr(items, "data", None) or []) if items else []
        metered_item_id: str | None = None
        for it in data:
            price = getattr(it, "price", None)
            rec = getattr(price, "recurring", None) if price else None
            ut = getattr(rec, "usage_type", None) if rec else None
            if ut == "metered":
                metered_item_id = str(getattr(it, "id", "") or "")
                break

        if not metered_item_id:
            log.info(
                "metered usage skip: user=%s stripe_sub=%s (no metered item)",
                user_id, side.stripe_subscription_id,
            )
            return {
                "status": "skipped_no_metered_item",
                "stripe_subscription_id": side.stripe_subscription_id,
            }

        # Stripe Python SDK v15 에서 ``UsageRecord`` 클래스가 제거됨 — REST POST 유지
        # (레거시 metered price + usage_records 엔드포인트).
        import httpx

        form: dict[str, str] = {
            "quantity": str(float(quantity)),
            "action": action,
        }
        if timestamp is not None:
            form["timestamp"] = str(int(timestamp))

        url = (
            f"https://api.stripe.com/v1/subscription_items/"
            f"{metered_item_id}/usage_records"
        )
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                data=form,
                headers={"Authorization": f"Bearer {self.config.secret_key}"},
            )
        if resp.status_code >= 400:
            log.error(
                "metered usage_records HTTP %s: %s",
                resp.status_code, resp.text[:500],
            )
            raise ValueError(
                f"stripe_usage_record_failed:{resp.status_code}:{resp.text[:200]}"
            )
        body = resp.json()
        ur_id = body.get("id")
        log.info(
            "metered UsageRecord: user=%s item=%s qty=%s action=%s id=%s",
            user_id, metered_item_id, quantity, action, ur_id,
        )
        return {
            "status": "recorded",
            "usage_record_id": ur_id,
            "subscription_item_id": metered_item_id,
        }

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
