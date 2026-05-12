"""``shared.notifications`` 단위 테스트 — config + service (Mock 0).

본 모듈은 외부 HTTP 호출이 없을 때의 동작 (등록·조회·폐기·dry-run 발송) 만
검증한다. 실 Expo Push HTTP 호출은 통합 테스트 (CoOps tests/test_notifications)
가 ``PUSH_HTTP_DISABLED=1`` 모드로 우회한다.
"""
from __future__ import annotations

import pytest

from notifications import PushConfig, PushDisabledError


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
