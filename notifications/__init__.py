"""``shared-libraries.notifications`` — 푸시 알림 도메인 (E-Day 4, 2026-05-13).

설계 원칙
=========
- **factory + DI** — ``shared.saas`` 와 동일 패턴. ``make_notification_models(Base, table_prefix)``
  가 서비스별 prefix (``coops_``, ``adk_``, ``medi_``) 로 ORM 을 생성.
- **provider 추상화** — Expo Push (`https://exp.host/--/api/v2/push/send`) 가 기본.
  FCM/APNs 직접 호출은 백로그 (Expo Push 의 한계가 보이면 swap).
- **graceful** — Expo 키 미설정 시 ``PushDisabledError`` 로 503 처리. 발송 자체는
  best-effort — 한 device 실패가 다른 device 발송을 막지 않음.
- **Mock 0** — 본 모듈에는 LLM 호출 / 외부 SDK 호출이 없다. Expo Push HTTP 호출은
  ``httpx.AsyncClient`` 1 회. 테스트는 ``PUSH_HTTP_DISABLED=1`` 로 ``send_to_devices``
  를 dry-run 화 가능 (DB 통로만 검증).
"""

from .config import PushConfig, PushDisabledError
from .models import make_notification_models
from .service import NotificationService

__all__ = [
    "PushConfig",
    "PushDisabledError",
    "make_notification_models",
    "NotificationService",
]
