"""``shared.notifications.inbox`` — In-app 알림 보관소 (E Round 2 Day 1).

Expo Push 발송과는 별개로 *서버 측에 영속화* 되는 인앱 알림. 모바일이 오프라인이거나
권한 거부 상태여도 다음 로그인 시 알림을 볼 수 있다.

설계:
    - ORM factory ``make_inbox_models(Base, table_prefix)`` — service prefix 별
      ``{prefix}notifications`` 테이블 1개.
    - ``InboxService`` 가 push 발송 직후 호출되어 같은 메시지를 DB 에 row 로 저장.
      mobile 의 ``GET /notifications/inbox`` 가 이 row 들을 read.
    - 카드 한 장에 ``kind`` (approval_request / approval_decision / ir_report / etc.) +
      ``ref_id`` (대응 도메인 객체 id) 를 두어 mobile 이 탭 → 라우팅 가능.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column


def make_inbox_models(Base, table_prefix: str = ""):
    """``Notification`` ORM 생성 — 사용자별 in-app 알림 1행 = 1알림."""
    p = table_prefix or ""
    tbl = f"{p}notifications"

    class Notification(Base):  # type: ignore[misc, valid-type]
        __tablename__ = tbl

        id: Mapped[str] = mapped_column(
            String(36),
            primary_key=True,
            default=lambda: str(uuid.uuid4()),
        )
        user_id: Mapped[str] = mapped_column(
            String(128), nullable=False, index=True
        )
        kind: Mapped[str] = mapped_column(
            String(48), nullable=False, index=True,
            comment="approval_request | approval_decision | ir_report | system",
        )
        title: Mapped[str] = mapped_column(String(200), nullable=False)
        body: Mapped[str] = mapped_column(Text, nullable=False)
        ref_id: Mapped[str | None] = mapped_column(
            String(64), nullable=True,
            comment="대응 도메인 객체 id (approval id, ir job id, ...)",
        )
        data_json: Mapped[str | None] = mapped_column(
            Text, nullable=True,
            comment="JSON 직렬화 (mobile 이 라우팅용 추가 정보).",
        )
        read: Mapped[bool] = mapped_column(
            Boolean, nullable=False, default=False, index=True
        )
        read_at: Mapped[datetime | None] = mapped_column(
            DateTime(timezone=True), nullable=True
        )
        created_at: Mapped[datetime] = mapped_column(
            DateTime(timezone=True), server_default=func.now(), nullable=False
        )

    return Notification
