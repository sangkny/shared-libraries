"""``shared.notifications.config`` — env 기반 Expo Push 설정 (E-Day 4)."""
from __future__ import annotations

import os
from dataclasses import dataclass


class PushDisabledError(RuntimeError):
    """``PUSH_ENABLED=0`` 이거나 핵심 키 미설정. 라우트는 503 으로 응답해야 한다."""


@dataclass
class PushConfig:
    """푸시 알림 환경 설정.

    ENV 변수 (공통):
        PUSH_ENABLED                : "1" 이면 활성 (기본 "0").
        EXPO_ACCESS_TOKEN           : Expo 푸시 API 인증 토큰 (선택; 없으면 익명 호출).
        PUSH_API_URL                : Expo Push endpoint (기본 https://exp.host/--/api/v2/push/send).
        PUSH_HTTP_DISABLED          : "1" 이면 외부 HTTP 호출 skip (테스트용 dry-run).

    서비스별 override 는 ``ADK_PUSH_*`` / ``COOPS_PUSH_*`` prefix.
    """

    enabled: bool = False
    expo_access_token: str | None = None
    api_url: str = "https://exp.host/--/api/v2/push/send"
    http_disabled: bool = False

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None, *, prefix: str = "") -> "PushConfig":
        env = env or dict(os.environ)
        p = prefix.upper().rstrip("_") + "_" if prefix else ""

        def _get(key: str, default: str = "") -> str:
            return env.get(f"{p}PUSH_{key}", env.get(f"PUSH_{key}", default))

        return cls(
            enabled=_get("ENABLED", "0") == "1",
            expo_access_token=(_get("EXPO_ACCESS_TOKEN", "") or None),
            api_url=_get("API_URL", "https://exp.host/--/api/v2/push/send"),
            http_disabled=_get("HTTP_DISABLED", "0") == "1",
        )

    def require_enabled(self) -> None:
        if not self.enabled:
            raise PushDisabledError("푸시 알림 비활성 (PUSH_ENABLED=0)")
