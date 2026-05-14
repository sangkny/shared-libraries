"""``shared.notifications`` 단위 테스트 — config + service + gateway (Mock 0).

본 모듈은 외부 HTTP 호출이 없을 때의 동작 (등록·조회·폐기·dry-run 발송) 만
검증한다. 실 Expo/FCM/APNs HTTP 호출은 통합 테스트가 ``PUSH_HTTP_DISABLED=1``
모드로 우회한다.

E R3 Day 1: gateway 추상화 단위 테스트 (Expo / FCM / APNs dry-run + factory
분기 + lazy import graceful failure) 가 추가됨.
"""
from __future__ import annotations

import pytest

from notifications import (
    APNsPushGateway,
    ExpoPushGateway,
    FCMPushGateway,
    PushConfig,
    PushDisabledError,
    PushMessage,
    make_gateway,
)


def test_push_config_default_disabled() -> None:
    cfg = PushConfig.from_env(env={})
    assert cfg.enabled is False
    assert cfg.http_disabled is False
    assert cfg.expo_access_token is None
    assert cfg.api_url == "https://exp.host/--/api/v2/push/send"


def test_push_config_enabled_via_env() -> None:
    cfg = PushConfig.from_env(
        env={
            "PUSH_ENABLED": "1",
            "PUSH_EXPO_ACCESS_TOKEN": "tok_xxx",
            "PUSH_HTTP_DISABLED": "1",
        }
    )
    assert cfg.enabled is True
    assert cfg.expo_access_token == "tok_xxx"
    assert cfg.http_disabled is True


def test_push_config_service_prefix_overrides_common() -> None:
    cfg = PushConfig.from_env(
        env={
            "PUSH_ENABLED": "0",
            "COOPS_PUSH_ENABLED": "1",
            "COOPS_PUSH_API_URL": "https://example.com/push",
        },
        prefix="COOPS",
    )
    assert cfg.enabled is True
    assert cfg.api_url == "https://example.com/push"


def test_push_config_require_enabled_raises_when_off() -> None:
    cfg = PushConfig.from_env(env={"PUSH_ENABLED": "0"})
    with pytest.raises(PushDisabledError):
        cfg.require_enabled()


def test_push_config_require_enabled_passes_when_on() -> None:
    cfg = PushConfig.from_env(env={"PUSH_ENABLED": "1"})
    cfg.require_enabled()  # 예외 X


# ── E R3 Day 1: gateway 단위 테스트 ─────────────────────────────────


def test_push_config_provider_default_is_expo() -> None:
    cfg = PushConfig.from_env(env={"PUSH_ENABLED": "1"})
    assert cfg.provider == "expo"


def test_push_config_provider_fcm_env() -> None:
    cfg = PushConfig.from_env(
        env={
            "PUSH_ENABLED": "1",
            "PUSH_PROVIDER": "fcm",
            "FCM_PROJECT_ID": "test-proj",
            "FCM_SERVICE_ACCOUNT_JSON": '{"client_email":"x@y","private_key":"k"}',
        }
    )
    assert cfg.provider == "fcm"
    assert cfg.fcm_project_id == "test-proj"
    assert cfg.fcm_service_account_json is not None


def test_push_config_provider_apns_env() -> None:
    cfg = PushConfig.from_env(
        env={
            "PUSH_ENABLED": "1",
            "PUSH_PROVIDER": "apns",
            "APNS_TEAM_ID": "TEAMID",
            "APNS_KEY_ID": "KEYID",
            "APNS_P8": "-----BEGIN PRIVATE KEY-----\n...",
            "APNS_BUNDLE_ID": "com.example.app",
            "APNS_USE_SANDBOX": "1",
        }
    )
    assert cfg.provider == "apns"
    assert cfg.apns_team_id == "TEAMID"
    assert cfg.apns_bundle_id == "com.example.app"
    assert cfg.apns_use_sandbox is True


def test_make_gateway_returns_expo_by_default() -> None:
    cfg = PushConfig.from_env(env={"PUSH_ENABLED": "1"})
    gw = make_gateway(cfg)
    assert isinstance(gw, ExpoPushGateway)
    assert gw.provider == "expo"


def test_make_gateway_returns_fcm_when_configured() -> None:
    cfg = PushConfig.from_env(
        env={"PUSH_ENABLED": "1", "PUSH_PROVIDER": "fcm"}
    )
    gw = make_gateway(cfg)
    assert isinstance(gw, FCMPushGateway)
    assert gw.provider == "fcm"


def test_make_gateway_returns_apns_when_configured() -> None:
    cfg = PushConfig.from_env(
        env={"PUSH_ENABLED": "1", "PUSH_PROVIDER": "apns"}
    )
    gw = make_gateway(cfg)
    assert isinstance(gw, APNsPushGateway)
    assert gw.provider == "apns"


@pytest.mark.asyncio
async def test_expo_gateway_dry_run_returns_skipped() -> None:
    cfg = PushConfig.from_env(env={"PUSH_ENABLED": "1", "PUSH_HTTP_DISABLED": "1"})
    gw = ExpoPushGateway(cfg)
    msgs = [
        PushMessage(token="ExponentPushToken[xxx]", title="t", body="b"),
        PushMessage(token="ExponentPushToken[yyy]", title="t", body="b"),
    ]
    res = await gw.send_batch(msgs)
    assert res.sent == 0
    assert res.failed == 0
    assert res.skipped == 2
    assert len(res.tokens) == 2


@pytest.mark.asyncio
async def test_fcm_gateway_dry_run_returns_skipped() -> None:
    cfg = PushConfig.from_env(
        env={
            "PUSH_ENABLED": "1",
            "PUSH_PROVIDER": "fcm",
            "PUSH_HTTP_DISABLED": "1",
            "FCM_PROJECT_ID": "p",
            "FCM_SERVICE_ACCOUNT_JSON": '{"client_email":"x"}',
        }
    )
    gw = FCMPushGateway(cfg)
    res = await gw.send_batch([PushMessage(token="fcm_dev_tok", title="t", body="b")])
    assert res.skipped == 1
    assert res.failed == 0


@pytest.mark.asyncio
async def test_apns_gateway_dry_run_returns_skipped() -> None:
    cfg = PushConfig.from_env(
        env={
            "PUSH_ENABLED": "1",
            "PUSH_PROVIDER": "apns",
            "PUSH_HTTP_DISABLED": "1",
            "APNS_BUNDLE_ID": "com.example.app",
        }
    )
    gw = APNsPushGateway(cfg)
    res = await gw.send_batch([PushMessage(token="apns_dev_tok", title="t", body="b")])
    assert res.skipped == 1
    assert res.failed == 0


@pytest.mark.asyncio
async def test_fcm_gateway_fails_gracefully_without_project_id() -> None:
    cfg = PushConfig.from_env(
        env={
            "PUSH_ENABLED": "1",
            "PUSH_PROVIDER": "fcm",
            # FCM_PROJECT_ID 없음 - HTTP_DISABLED 도 아님
        }
    )
    gw = FCMPushGateway(cfg)
    res = await gw.send_batch([PushMessage(token="t", title="t", body="b")])
    assert res.failed == 1
    assert res.sent == 0
    assert "fcm_project_id" in (res.detail or "")


@pytest.mark.asyncio
async def test_apns_gateway_fails_gracefully_without_bundle_id() -> None:
    cfg = PushConfig.from_env(
        env={
            "PUSH_ENABLED": "1",
            "PUSH_PROVIDER": "apns",
            # APNS_BUNDLE_ID 없음
        }
    )
    gw = APNsPushGateway(cfg)
    res = await gw.send_batch([PushMessage(token="t", title="t", body="b")])
    assert res.failed == 1
    assert "bundle_id" in (res.detail or "")


@pytest.mark.asyncio
async def test_empty_batch_returns_zero() -> None:
    cfg = PushConfig.from_env(env={"PUSH_ENABLED": "1"})
    gw = ExpoPushGateway(cfg)
    res = await gw.send_batch([])
    assert res.sent == 0 and res.failed == 0 and res.skipped == 0
