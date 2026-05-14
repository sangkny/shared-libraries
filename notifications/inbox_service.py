"""``shared.notifications.inbox_service`` — In-app 알림 CRUD (E Round 2 Day 1).

``NotificationService.send_to_user`` 가 Expo Push 직후 호출하는 대상.
모바일 ``GET /notifications/inbox`` 라우트는 본 서비스의 ``list_for_user`` 를 호출.

E R3-Day 4: ``purge_older_than`` 을 추가해 오래된 알림 자동 정리를 지원
(retention policy). 기본은 읽은 알림만 삭제 — 미독 알림은 사용자가 확인할 때까지 보존.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from sqlalchemy import delete, select, update
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

    # ── Retention (E R3-Day 4) ────────────────────────────────────

    async def purge_older_than(
        self,
        db: AsyncSession,
        *,
        days: int,
        include_unread: bool = False,
    ) -> int:
        """``days`` 일 이전 생성된 알림 삭제. 기본은 read=True 만 (안전).

        Args:
            days: 보존 기간 (일). 본 시점 - days 이전 created_at 이 삭제 대상.
            include_unread: True 면 미독 알림도 함께 삭제 (강제 정리). 운영에서는
                기본 ``False`` 유지 — 사용자가 확인하지 않은 알림이 사라지는 것은
                위험 (특히 진단/결재).

        Returns:
            삭제된 row 수.

        Notes:
            - DB-level CASCADE 가 없으므로 ``ref_id`` 가 가리키는 도메인 객체는
              영향 받지 않는다.
            - 매우 큰 테이블이면 batch 로 끊어서 (e.g., 10k row 씩) 호출 권장.
              본 구현은 단일 DELETE — sub-millisecond 인덱스 스캔 (created_at).
        """
        if days < 0:
            raise ValueError(f"days 는 음수일 수 없음: {days}")
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        stmt = delete(self.Notification).where(
            self.Notification.created_at < cutoff
        )
        if not include_unread:
            stmt = stmt.where(self.Notification.read.is_(True))
        res = await db.execute(stmt)
        deleted = int(res.rowcount or 0)
        if deleted > 0:
            log.info(
                "inbox_purge service=%s deleted=%d cutoff=%s include_unread=%s",
                self.service_name or "?", deleted, cutoff.isoformat(),
                include_unread,
            )
        return deleted


__all__ = ["InboxService"]
