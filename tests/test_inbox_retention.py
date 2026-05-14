"""``shared.notifications.inbox_service.purge_older_than`` + retention loop 단위 테스트.

E R3-Day 4 — Mock 0. In-memory SQLite + ``make_inbox_models`` 로 격리된 ORM 을
생성하고 시간차 row 를 직접 시드해 retention 동작을 검증한다.

본 모듈은 외부 의존성 (Postgres / 도커) 이 없어 CI 에서 즉시 통과한다.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from notifications import (
    InboxService,
    PushConfig,
    make_inbox_models,
    run_retention_cycle,
)


# ── 테스트 ORM 격리 ───────────────────────────────────────────


class _Base(DeclarativeBase):
    pass


_Notification = make_inbox_models(_Base, table_prefix="t_")


@pytest.fixture
async def session_maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


@pytest.fixture
def inbox() -> InboxService:
    return InboxService(notification_cls=_Notification, service_name="test")


async def _seed(
    db: AsyncSession,
    *,
    user_id: str = "u",
    kind: str = "system",
    title: str = "t",
    body: str = "b",
    read: bool = False,
    created_at: datetime | None = None,
) -> str:
    row = _Notification(
        user_id=user_id,
        kind=kind,
        title=title,
        body=body,
        read=read,
    )
    if created_at is not None:
        row.created_at = created_at
    db.add(row)
    await db.flush()
    return row.id


# ── 단위 테스트 ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_purge_older_than_negative_days_raises(
    session_maker, inbox: InboxService
) -> None:
    async with session_maker() as db:
        with pytest.raises(ValueError):
            await inbox.purge_older_than(db, days=-1)


@pytest.mark.asyncio
async def test_purge_older_than_keeps_recent_rows(
    session_maker, inbox: InboxService
) -> None:
    async with session_maker() as db:
        # 100일 전 (read=True) → 삭제 대상
        old_id = await _seed(
            db, read=True, created_at=datetime.now(timezone.utc) - timedelta(days=100),
        )
        # 10일 전 (read=True) → 보존
        recent_id = await _seed(
            db, read=True, created_at=datetime.now(timezone.utc) - timedelta(days=10),
        )
        deleted = await inbox.purge_older_than(db, days=90)
        assert deleted == 1
        rows = await inbox.list_for_user(db, user_id="u", limit=100)
        ids = {r.id for r in rows}
        assert recent_id in ids
        assert old_id not in ids


@pytest.mark.asyncio
async def test_purge_older_than_skips_unread_by_default(
    session_maker, inbox: InboxService
) -> None:
    async with session_maker() as db:
        # 100일 전, 미독 → 기본은 보존
        unread_old = await _seed(
            db, read=False, created_at=datetime.now(timezone.utc) - timedelta(days=100),
        )
        # 100일 전, 읽음 → 삭제
        read_old = await _seed(
            db, read=True, created_at=datetime.now(timezone.utc) - timedelta(days=100),
        )
        deleted = await inbox.purge_older_than(db, days=90)
        assert deleted == 1
        rows = await inbox.list_for_user(db, user_id="u", limit=100)
        ids = {r.id for r in rows}
        assert unread_old in ids
        assert read_old not in ids


@pytest.mark.asyncio
async def test_purge_older_than_include_unread_deletes_both(
    session_maker, inbox: InboxService
) -> None:
    async with session_maker() as db:
        unread_old = await _seed(
            db, read=False, created_at=datetime.now(timezone.utc) - timedelta(days=100),
        )
        read_old = await _seed(
            db, read=True, created_at=datetime.now(timezone.utc) - timedelta(days=100),
        )
        deleted = await inbox.purge_older_than(db, days=90, include_unread=True)
        assert deleted == 2
        rows = await inbox.list_for_user(db, user_id="u", limit=100)
        ids = {r.id for r in rows}
        assert unread_old not in ids
        assert read_old not in ids


@pytest.mark.asyncio
async def test_purge_older_than_days_zero_purges_everything(
    session_maker, inbox: InboxService
) -> None:
    """days=0 이면 *지금 이전* 모두 삭제 (read 만)."""
    async with session_maker() as db:
        await _seed(db, read=True)
        await _seed(db, read=False)
        deleted = await inbox.purge_older_than(db, days=0)
        assert deleted == 1  # read=True 1개만 (미독 1개 보존)


@pytest.mark.asyncio
async def test_purge_zero_when_nothing_old(
    session_maker, inbox: InboxService
) -> None:
    async with session_maker() as db:
        await _seed(
            db, read=True, created_at=datetime.now(timezone.utc) - timedelta(days=5),
        )
        deleted = await inbox.purge_older_than(db, days=90)
        assert deleted == 0


# ── retention loop helper ────────────────────────────────────


@pytest.mark.asyncio
async def test_run_retention_cycle_noop_when_disabled(
    session_maker, inbox: InboxService
) -> None:
    cfg = PushConfig.from_env(env={"PUSH_ENABLED": "1"})
    assert cfg.inbox_retention_enabled is False
    async with session_maker() as db:
        await _seed(
            db, read=True, created_at=datetime.now(timezone.utc) - timedelta(days=400),
        )
        await db.commit()
    deleted = await run_retention_cycle(inbox, cfg, session_maker)
    assert deleted == 0  # disabled → noop


@pytest.mark.asyncio
async def test_run_retention_cycle_deletes_when_enabled(
    session_maker, inbox: InboxService
) -> None:
    cfg = PushConfig.from_env(
        env={
            "PUSH_ENABLED": "1",
            "INBOX_RETENTION_ENABLED": "1",
            "INBOX_RETENTION_DAYS": "30",
        }
    )
    assert cfg.inbox_retention_enabled is True
    assert cfg.inbox_retention_days == 30

    async with session_maker() as db:
        await _seed(
            db, read=True, created_at=datetime.now(timezone.utc) - timedelta(days=60),
        )
        await _seed(
            db, read=True, created_at=datetime.now(timezone.utc) - timedelta(days=5),
        )
        await db.commit()

    deleted = await run_retention_cycle(inbox, cfg, session_maker)
    assert deleted == 1


@pytest.mark.asyncio
async def test_run_retention_cycle_respects_include_unread(
    session_maker, inbox: InboxService
) -> None:
    cfg = PushConfig.from_env(
        env={
            "PUSH_ENABLED": "1",
            "INBOX_RETENTION_ENABLED": "1",
            "INBOX_RETENTION_DAYS": "30",
            "INBOX_RETENTION_INCLUDE_UNREAD": "1",
        }
    )
    assert cfg.inbox_retention_include_unread is True

    async with session_maker() as db:
        await _seed(
            db, read=False, created_at=datetime.now(timezone.utc) - timedelta(days=100),
        )
        await db.commit()

    deleted = await run_retention_cycle(inbox, cfg, session_maker)
    assert deleted == 1
