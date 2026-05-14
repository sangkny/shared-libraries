"""shared-libraries/saas/stripe_service — 순수 함수 + parse_event 단위 테스트.

테스트 철학 (Mock 0):
    - LLM/네트워크 mock 금지
    - HTTP / stripe SDK 호출은 단위 테스트 범위 밖 (ADK/CoOps 통합 테스트에서 검증)
    - 본 모듈은 ``StripeConfig.from_env`` (env 파싱) + ``parse_event`` (서명 우회시
      JSON 파싱 결정성) + ``handle_event`` 의 라우팅을 검증.
    - handle_event 의 DB 영속화는 ADK/CoOps 통합 테스트에서 fresh AsyncSession 으로 검증.
    - dispute 라우팅만 ``AsyncSession`` 최소 스텁(``AsyncMock``) — DB row 없이 메트릭·
      반환 action 검증 (네트워크/Stripe SDK 미호출).
"""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from saas import StripeConfig, StripeSignatureError, STRIPE_SUPPORTED_EVENTS
from saas.schemas import StripeCheckoutRequest
from saas.stripe_service import StripeService


# ── StripeConfig.from_env ─────────────────────────────────────


def test_stripe_config_default_disabled() -> None:
    with patch.dict(os.environ, {}, clear=True):
        cfg = StripeConfig.from_env()
    assert cfg.enabled is False
    assert cfg.secret_key is None


def test_stripe_config_enabled_requires_secret_key() -> None:
    with patch.dict(
        os.environ, {"STRIPE_ENABLED": "1"}, clear=True
    ):
        cfg = StripeConfig.from_env()
    assert cfg.enabled is False, "secret_key 없으면 토글이 1이어도 disabled"


def test_stripe_config_enabled_with_secret() -> None:
    env = {
        "STRIPE_ENABLED": "1",
        "STRIPE_SECRET_KEY": "sk_test_dummy",
        "STRIPE_WEBHOOK_SECRET": "whsec_dummy",
        "STRIPE_PUBLIC_KEY": "pk_test_dummy",
    }
    with patch.dict(os.environ, env, clear=True):
        cfg = StripeConfig.from_env()
    assert cfg.enabled is True
    assert cfg.webhook_secret == "whsec_dummy"
    assert cfg.public_key == "pk_test_dummy"


def test_stripe_config_prefix_overrides_global() -> None:
    env = {
        "STRIPE_ENABLED": "0",
        "ADK_STRIPE_ENABLED": "1",
        "ADK_STRIPE_SECRET_KEY": "sk_test_adk",
    }
    with patch.dict(os.environ, env, clear=True):
        cfg = StripeConfig.from_env(prefix="ADK_")
    assert cfg.enabled is True
    assert cfg.secret_key == "sk_test_adk"


def test_stripe_config_skip_signature_verify_flag() -> None:
    env = {
        "STRIPE_ENABLED": "1",
        "STRIPE_SECRET_KEY": "sk_test_dummy",
        "STRIPE_SKIP_SIG_VERIFY": "1",
    }
    with patch.dict(os.environ, env, clear=True):
        cfg = StripeConfig.from_env()
    assert cfg.skip_signature_verification is True


# ── parse_event (서명 우회 모드 — 테스트 전용) ────────────────


def _svc_skip_sig() -> StripeService:
    cfg = StripeConfig(
        enabled=True, secret_key="sk_test", webhook_secret="whsec_test",
        skip_signature_verification=True,
    )
    return StripeService(
        config=cfg,
        billing=None,  # parse_event 는 billing 미사용
        plan_mapping_cls=None,
        stripe_subscription_cls=None,
    )


def test_parse_event_skip_sig_mode_parses_json() -> None:
    svc = _svc_skip_sig()
    payload = {"type": "checkout.session.completed", "data": {"object": {}}}
    event = svc.parse_event(json.dumps(payload).encode("utf-8"))
    assert event["type"] == "checkout.session.completed"


def test_parse_event_skip_sig_mode_invalid_json_raises() -> None:
    svc = _svc_skip_sig()
    with pytest.raises(StripeSignatureError):
        svc.parse_event(b"not-a-json-payload")


def test_parse_event_live_missing_webhook_secret_raises() -> None:
    cfg = StripeConfig(enabled=True, secret_key="sk_test", webhook_secret=None)
    svc = StripeService(
        config=cfg, billing=None,
        plan_mapping_cls=None, stripe_subscription_cls=None,
    )
    with pytest.raises(StripeSignatureError):
        svc.parse_event(b'{"type":"x"}', signature_header="t=1,v1=deadbeef")


def test_stripe_config_r3_slack_and_fx_from_env() -> None:
    env = {
        "STRIPE_ENABLED": "1",
        "STRIPE_SECRET_KEY": "sk_test_dummy",
        "STRIPE_SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/XXX/YYY/ZZZ",
        "STRIPE_FX_EUR_USD": "1.10",
        "STRIPE_FX_JPY_USD": "0.007",
        "STRIPE_FX_KRW_USD": "0.0008",
    }
    with patch.dict(os.environ, env, clear=True):
        cfg = StripeConfig.from_env()
    assert cfg.slack_webhook_url is not None
    assert "hooks.slack.com" in cfg.slack_webhook_url
    assert cfg.fx_eur_usd == 1.10
    assert cfg.fx_jpy_usd == 0.007
    assert cfg.fx_krw_usd == 0.0008


def test_checkout_request_rejects_promo_and_coupon_together() -> None:
    with pytest.raises(ValidationError):
        StripeCheckoutRequest(
            plan_code="startup",
            promotion_code="promo_123",
            coupon_id="coupon_abc",
        )


@pytest.mark.asyncio
async def test_handle_event_dispute_created() -> None:
    cfg = StripeConfig(
        enabled=True,
        secret_key="sk_test",
        skip_signature_verification=True,
    )

    class _B:
        service_name = "unit_test"

    svc = StripeService(
        config=cfg,
        billing=_B(),
        plan_mapping_cls=object,
        stripe_subscription_cls=object,
    )
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)
    db.flush = AsyncMock()
    ev = {
        "type": "charge.dispute.created",
        "data": {
            "object": {
                "id": "dp_test",
                "status": "needs_response",
                "charge": "ch_test",
                "amount": 5000,
                "currency": "krw",
            }
        },
    }
    out = await svc.handle_event(db, ev)
    assert out["received"] is True
    assert out["type"] == "charge.dispute.created"
    assert out["action"].startswith("dispute_")


def test_parse_event_live_missing_signature_raises() -> None:
    cfg = StripeConfig(
        enabled=True, secret_key="sk_test", webhook_secret="whsec_test"
    )
    svc = StripeService(
        config=cfg, billing=None,
        plan_mapping_cls=None, stripe_subscription_cls=None,
    )
    with pytest.raises(StripeSignatureError):
        svc.parse_event(b'{"type":"x"}', signature_header=None)


# ── 지원 이벤트 집합 ────────────────────────────────────────


def test_supported_events_includes_round1_and_round2() -> None:
    """B-7 Round 2 (2026-05-13) — invoice/refund 3종 추가."""
    assert STRIPE_SUPPORTED_EVENTS == {
        # Round 1
        "checkout.session.completed",
        "customer.subscription.updated",
        "customer.subscription.deleted",
        # Round 2
        "invoice.paid",
        "invoice.payment_failed",
        "charge.refunded",
        # Round 3
        "charge.dispute.created",
        "charge.dispute.closed",
        "charge.dispute.funds_withdrawn",
        "charge.dispute.funds_reinstated",
    }
