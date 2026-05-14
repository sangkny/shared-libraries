"""``shared-libraries.notifications`` — 푸시 알림 도메인 (E-Day 4, 2026-05-13).

설계 원칙
=========
- **factory + DI** — ``shared.saas`` 와 동일 패턴. ``make_notification_models(Base, table_prefix)``
  가 서비스별 prefix (``coops_``, ``adk_``, ``medi_``) 로 ORM 을 생성.
- **provider 추상화 (E R3)** — ``PushGateway`` 인터페이스로 Expo / FCM HTTP v1 /
  APNs HTTP/2 를 swap. ``PUSH_PROVIDER=expo|fcm|apns`` 환경 변수로 선택. 외부
  SDK (PyJWT, h2) 는 lazy import — Expo 만 쓰는 환경엔 추가 의존성 없음.
- **graceful** — Expo 키 미설정 시 ``PushDisabledError`` 로 503 처리. 발송 자체는
  best-effort — 한 device 실패가 다른 device 발송을 막지 않음.
- **Mock 0** — 본 모듈에는 LLM 호출 / 외부 SDK 호출이 없다. HTTP 호출은
  ``httpx.AsyncClient``. 테스트는 ``PUSH_HTTP_DISABLED=1`` 로 모든 gateway 를
  dry-run 화 가능 (DB 통로만 검증).
"""

from .config import PushConfig, PushDisabledError
from .gateway import (
    APNsPushGateway,
    ExpoPushGateway,
    FCMPushGateway,
    PushGateway,
    PushMessage,
    PushSendResult,
    make_gateway,
)
from .inbox_models import make_inbox_models
from .inbox_service import InboxService
from .models import make_notification_models
from .retention import (
    run_retention_cycle,
    start_retention_loop,
    stop_retention_loop,
)
from .service import NotificationService

__all__ = [
    "PushConfig",
    "PushDisabledError",
    "PushGateway",
    "PushMessage",
    "PushSendResult",
    "ExpoPushGateway",
    "FCMPushGateway",
    "APNsPushGateway",
    "make_gateway",
    "make_notification_models",
    "NotificationService",
    "make_inbox_models",
    "InboxService",
    "run_retention_cycle",
    "start_retention_loop",
    "stop_retention_loop",
]
