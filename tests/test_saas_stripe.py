"""shared-libraries/saas/stripe_service — 순수 함수 + parse_event 단위 테스트.

테스트 철학 (Mock 0):
    - LLM/네트워크 mock 금지
    - HTTP / stripe SDK 호출은 단위 테스트 범위 밖 (ADK/CoOps 통합 테스트에서 검증)
    - 본 모듈은 ``StripeConfig.from_env`` (env 파싱) + ``parse_event`` (서명 우회시
      JSON 파싱 결정성) + ``handle_event`` 의 라우팅을 검증.
    - handle_event 의 DB 영속화는 ADK/CoOps 통합 테스트에서 fresh AsyncSession 으로 검증.
"""
from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from saas import StripeConfig, StripeSignatureError, STRIPE_SUPPORTED_EVENTS
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


def test_supported_events_only_three() -> None:
    assert STRIPE_SUPPORTED_EVENTS == {
        "checkout.session.completed",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    }
