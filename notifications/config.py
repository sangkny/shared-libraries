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
        PUSH_PROVIDER               : "expo" (기본) | "fcm" | "apns"
        EXPO_ACCESS_TOKEN           : Expo 푸시 API 인증 토큰 (선택; 없으면 익명 호출).
        PUSH_API_URL                : Expo Push endpoint (기본 https://exp.host/--/api/v2/push/send).
        PUSH_HTTP_DISABLED          : "1" 이면 외부 HTTP 호출 skip (테스트용 dry-run).

    FCM (PUSH_PROVIDER=fcm):
        FCM_PROJECT_ID                  : Firebase project id.
        FCM_SERVICE_ACCOUNT_JSON        : 서비스 어카운트 JSON (raw 문자열 또는 파일 경로).

    APNs (PUSH_PROVIDER=apns):
        APNS_TEAM_ID                    : Apple Developer Team ID.
        APNS_KEY_ID                     : .p8 키의 Key ID.
        APNS_P8                         : .p8 키 내용 (PEM) 또는 파일 경로.
        APNS_BUNDLE_ID                  : iOS 앱 bundle id (apns-topic 으로 사용).
        APNS_USE_SANDBOX                : "1" 이면 sandbox endpoint (개발용).

    Inbox retention (E R3-Day 4):
        INBOX_RETENTION_ENABLED         : "1" 이면 자동 purge 스케줄러 활성 (기본 "0").
        INBOX_RETENTION_DAYS            : 보존 일수 (기본 90).
        INBOX_RETENTION_INCLUDE_UNREAD  : "1" 이면 미독 알림도 함께 삭제 (기본 "0").
        INBOX_RETENTION_INTERVAL_HOURS  : 스케줄러 실행 주기 (시간, 기본 24).

    서비스별 override 는 ``ADK_PUSH_*`` / ``COOPS_PUSH_*`` / ``MEDI_PUSH_*`` prefix.
    """

    enabled: bool = False
    provider: str = "expo"
    expo_access_token: str | None = None
    api_url: str = "https://exp.host/--/api/v2/push/send"
    http_disabled: bool = False

    # FCM
    fcm_project_id: str | None = None
    fcm_service_account_json: str | None = None

    # APNs
    apns_team_id: str | None = None
    apns_key_id: str | None = None
    apns_p8: str | None = None
    apns_bundle_id: str | None = None
    apns_use_sandbox: bool = False

    # Inbox retention
    inbox_retention_enabled: bool = False
    inbox_retention_days: int = 90
    inbox_retention_include_unread: bool = False
    inbox_retention_interval_hours: int = 24

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None, *, prefix: str = "") -> "PushConfig":
        env = env or dict(os.environ)
        p = prefix.upper().rstrip("_") + "_" if prefix else ""

        def _push(key: str, default: str = "") -> str:
            return env.get(f"{p}PUSH_{key}", env.get(f"PUSH_{key}", default))

        def _scoped(key: str, default: str = "") -> str:
            """prefixed 우선, prefixed 없으면 plain (FCM/APNs 키용)."""
            return env.get(f"{p}{key}", env.get(key, default))

        return cls(
            enabled=_push("ENABLED", "0") == "1",
            provider=(_push("PROVIDER", "expo") or "expo").lower(),
            expo_access_token=(_push("EXPO_ACCESS_TOKEN", "") or None),
            api_url=_push("API_URL", "https://exp.host/--/api/v2/push/send"),
            http_disabled=_push("HTTP_DISABLED", "0") == "1",
            fcm_project_id=(_scoped("FCM_PROJECT_ID", "") or None),
            fcm_service_account_json=(_scoped("FCM_SERVICE_ACCOUNT_JSON", "") or None),
            apns_team_id=(_scoped("APNS_TEAM_ID", "") or None),
            apns_key_id=(_scoped("APNS_KEY_ID", "") or None),
            apns_p8=(_scoped("APNS_P8", "") or None),
            apns_bundle_id=(_scoped("APNS_BUNDLE_ID", "") or None),
            apns_use_sandbox=_scoped("APNS_USE_SANDBOX", "0") == "1",
            inbox_retention_enabled=_scoped("INBOX_RETENTION_ENABLED", "0") == "1",
            inbox_retention_days=int(_scoped("INBOX_RETENTION_DAYS", "90") or 90),
            inbox_retention_include_unread=(
                _scoped("INBOX_RETENTION_INCLUDE_UNREAD", "0") == "1"
            ),
            inbox_retention_interval_hours=int(
                _scoped("INBOX_RETENTION_INTERVAL_HOURS", "24") or 24
            ),
        )

    def require_enabled(self) -> None:
        if not self.enabled:
            raise PushDisabledError("푸시 알림 비활성 (PUSH_ENABLED=0)")
