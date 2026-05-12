"""``shared.notifications.inbox_service`` — In-app 알림 CRUD (E Round 2 Day 1).

``NotificationService.send_to_user`` 가 Expo Push 직후 호출하는 대상.
모바일 ``GET /notifications/inbox`` 라우트는 본 서비스의 ``list_for_user`` 를 호출.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession


log = logging.getLogger("notifications.inbox")


class InboxService:
    """ORM DI — ``InboxService(notification_cls=PushNotification)``."""

    def __init__(self, *, notification_cls, service_name: str | None = None) -> None:
        self.Notification = notification_cls
        self.service_name = service_name

    # ── 생성 ─────────────────────────────────────────────────────────

    async def create(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        kind: str,
        title: str,
        body: str,
        ref_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> Any:
        """알림 1건 저장. ``send_to_user`` 와 함께 호출하면 push + in-app 보장."""
        row = self.Notification(
            user_id=user_id,
            kind=kind,
            title=title,
            body=body,
            ref_id=ref_id,
            data_json=json.dumps(data, ensure_ascii=False) if data is not None else None,
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)
        return row

    # ── 조회 ─────────────────────────────────────────────────────────

    async def list_for_user(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        unread_only: bool = False,
        limit: int = 50,
    ) -> Sequence[Any]:
        stmt = (
            select(self.Notification)
            .where(self.Notification.user_id == user_id)
            .order_by(self.Notification.created_at.desc())
            .limit(int(limit))
        )
        if unread_only:
            stmt = stmt.where(self.Notification.read.is_(False))
        rows = await db.scalars(stmt)
        return list(rows)

    async def unread_count(self, db: AsyncSession, *, user_id: str) -> int:
        from sqlalchemy import func

        stmt = (
            select(func.count(self.Notification.id))
            .where(self.Notification.user_id == user_id)
            .where(self.Notification.read.is_(False))
        )
        return int(await db.scalar(stmt) or 0)

    # ── 읽음 처리 ────────────────────────────────────────────────────

    async def mark_read(
        self, db: AsyncSession, *, notification_id: str, user_id: str
    ) -> bool:
        """본인 소유 알림만 읽음 처리. 다른 user 의 id 는 silently false 반환."""
        res = await db.execute(
            update(self.Notification)
            .where(self.Notification.id == notification_id)
            .where(self.Notification.user_id == user_id)
            .where(self.Notification.read.is_(False))
            .values(read=True, read_at=datetime.now(timezone.utc))
        )
        return res.rowcount > 0

    async def mark_all_read(self, db: AsyncSession, *, user_id: str) -> int:
        res = await db.execute(
            update(self.Notification)
            .where(self.Notification.user_id == user_id)
            .where(self.Notification.read.is_(False))
            .values(read=True, read_at=datetime.now(timezone.utc))
        )
        return res.rowcount


__all__ = ["InboxService"]
