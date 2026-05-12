"""``shared.notifications.models`` — Push device ORM factory.

ADK / CoOps / MEDI 가 각각 ``adk_push_devices`` / ``coops_push_devices`` /
``medi_push_devices`` 등 prefix 로 자신만의 테이블을 갖는다 (charge isolation).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column


def make_notification_models(Base, table_prefix: str = ""):
    """``PushDevice`` ORM 생성 — Expo push token 단위로 row 1개.

    Notes:
        - ``expo_push_token`` 은 unique (단말기 1대 = row 1).
        - ``user_id`` 는 string (서비스의 인증 시스템이 user_id 형식을 결정).
        - ``revoked_at`` 이 NULL 인 row 만 발송 대상.
    """
    p = table_prefix or ""
    tbl = f"{p}push_devices"

    class PushDevice(Base):  # type: ignore[misc, valid-type]
        __tablename__ = tbl

        id: Mapped[str] = mapped_column(
            String(36),
            primary_key=True,
            default=lambda: str(uuid.uuid4()),
        )
        user_id: Mapped[str] = mapped_column(
            String(128), nullable=False, index=True
        )
        expo_push_token: Mapped[str] = mapped_column(
            String(256), nullable=False, unique=True, index=True
        )
        platform: Mapped[str] = mapped_column(
            String(16), nullable=False, default="unknown",
            comment="ios | android | web",
        )
        device_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
        active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
        created_at: Mapped[datetime] = mapped_column(
            DateTime(timezone=True), server_default=func.now(), nullable=False
        )
        last_seen_at: Mapped[datetime] = mapped_column(
            DateTime(timezone=True),
            server_default=func.now(),
            onupdate=func.now(),
            nullable=False,
        )
        revoked_at: Mapped[datetime | None] = mapped_column(
            DateTime(timezone=True), nullable=True
        )

    return PushDevice
