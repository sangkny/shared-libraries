"""``shared.notifications.service`` — 디바이스 등록/조회/폐기 + Expo Push 발송.

설계 결정 (2026-05-13):
    - ``register_device`` 는 idempotent — 이미 같은 ``expo_push_token`` 행이
      있으면 update (user_id 재할당 + last_seen_at). 단말기 한 대가 user 를 바꿀
      때도 동일 row 가 재사용.
    - ``send_to_user`` 는 best-effort — 발송 결과 (성공/실패 count) 만 dict 로
      반환. 한 device 실패가 다른 device 발송을 막지 않는다.
    - 외부 HTTP 호출 (httpx) 은 lazy import — config.http_disabled 면 skip 하고
      dry-run 결과만 반환. 단위 테스트가 외부 네트워크 없이 통과한다.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .config import PushConfig, PushDisabledError
from .gateway import PushGateway, PushMessage, PushSendResult, make_gateway
from .inbox_service import InboxService


log = logging.getLogger("notifications.service")


class NotificationService:
    """ORM DI + config DI — ADK/CoOps/MEDI 가 자기 prefix 의 ORM 으로 초기화."""

    def __init__(
        self,
        *,
        config: PushConfig,
        device_cls,
        service_name: str | None = None,
        inbox: InboxService | None = None,
        gateway: PushGateway | None = None,
    ) -> None:
        self.config = config
        self.Device = device_cls
        self.service_name = service_name
        self.inbox = inbox
        self.gateway = gateway or make_gateway(config)

    # ── 디바이스 관리 ────────────────────────────────────────────────

    async def register_device(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        expo_push_token: str,
        platform: str = "unknown",
        device_label: str | None = None,
    ) -> Any:
        """단말기 등록 또는 갱신 (idempotent).

        같은 token 이 다른 user 로 재등록되면 ``user_id`` 를 덮어쓴다 — 단말기
        소유자 변경 시 자동 정합성 유지.
        """
        existing = await db.scalar(
            select(self.Device).where(
                self.Device.expo_push_token == expo_push_token
            )
        )
        if existing is not None:
            existing.user_id = user_id
            existing.platform = platform or existing.platform or "unknown"
            existing.device_label = device_label or existing.device_label
            existing.active = True
            existing.revoked_at = None
            existing.last_seen_at = datetime.now(timezone.utc)
            await db.flush()
            await db.refresh(existing)
            return existing

        row = self.Device(
            user_id=user_id,
            expo_push_token=expo_push_token,
            platform=platform or "unknown",
            device_label=device_label,
            active=True,
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)
        return row

    async def revoke_device(
        self, db: AsyncSession, *, expo_push_token: str
    ) -> bool:
        """로그아웃 / 단말 분실 시 호출 — soft delete (active=False, revoked_at=now)."""
        res = await db.execute(
            update(self.Device)
            .where(self.Device.expo_push_token == expo_push_token)
            .values(active=False, revoked_at=datetime.now(timezone.utc))
        )
        return res.rowcount > 0

    async def list_active_for_user(
        self, db: AsyncSession, *, user_id: str
    ) -> list[Any]:
        rows = await db.scalars(
            select(self.Device)
            .where(self.Device.user_id == user_id)
            .where(self.Device.active.is_(True))
            .where(self.Device.revoked_at.is_(None))
        )
        return list(rows)

    # ── 발송 ────────────────────────────────────────────────────────

    async def send_to_user(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        title: str,
        body: str,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """사용자의 모든 활성 단말기에 푸시 발송 (provider-agnostic).

        실제 발송은 ``self.gateway`` 로 위임. 현행 default 는 Expo Push 이며
        ``PUSH_PROVIDER=fcm|apns`` 로 native FCM/APNs 직접 호출이 가능하다.

        Returns:
            ``{"sent": N, "failed": M, "skipped": K, "tokens": [...], "provider": ..., "detail": ...}``.
            ``skipped`` 은 ``PUSH_HTTP_DISABLED=1`` (dry-run) 모드일 때 nonzero.
        """
        self.config.require_enabled()
        devices = await self.list_active_for_user(db, user_id=user_id)
        if not devices:
            return {
                "sent": 0, "failed": 0, "skipped": 0,
                "tokens": [], "provider": self.gateway.provider,
            }

        messages = [
            PushMessage(
                token=d.expo_push_token,
                title=title,
                body=body,
                data=data or {},
                platform=getattr(d, "platform", None),
            )
            for d in devices
        ]
        try:
            result: PushSendResult = await self.gateway.send_batch(messages)
        except Exception as exc:
            log.exception("gateway_send_failed user_id=%s err=%s", user_id, exc)
            tokens = [m.token for m in messages]
            return {
                "sent": 0, "failed": len(tokens), "skipped": 0,
                "tokens": tokens, "provider": self.gateway.provider,
                "detail": f"gateway_exc: {exc}"[:300],
            }

        return {
            "sent": result.sent,
            "failed": result.failed,
            "skipped": result.skipped,
            "tokens": result.tokens,
            "provider": self.gateway.provider,
            "detail": result.detail,
        }

    # ── inbox 통합 helper ────────────────────────────────────────────

    async def notify(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        title: str,
        body: str,
        kind: str = "system",
        ref_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """In-app inbox 1행 저장 + (push 가능하면) Expo Push 발송.

        Push 가 비활성(``PUSH_ENABLED=0``)이거나 단말기 0대여도 inbox 는 항상 저장된다.
        반환값: ``{"inbox_id": str | None, "push": {sent, failed, skipped, tokens}}``.
        """
        inbox_id: str | None = None
        if self.inbox is not None:
            row = await self.inbox.create(
                db,
                user_id=user_id,
                kind=kind,
                title=title,
                body=body,
                ref_id=ref_id,
                data=data,
            )
            inbox_id = getattr(row, "id", None)

        push_result: dict[str, Any] = {
            "sent": 0, "failed": 0, "skipped": 0, "tokens": [], "disabled": False,
        }
        if self.config.enabled:
            try:
                push_result = await self.send_to_user(
                    db, user_id=user_id, title=title, body=body, data=data,
                )
            except PushDisabledError:
                push_result["disabled"] = True
            except Exception as exc:
                log.exception("notify_push_failed user_id=%s err=%s", user_id, exc)
                push_result["failed"] = -1
        else:
            push_result["disabled"] = True

        return {"inbox_id": inbox_id, "push": push_result}


__all__ = ["NotificationService"]
