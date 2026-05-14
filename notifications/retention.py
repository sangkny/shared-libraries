"""``shared.notifications.retention`` — Inbox 자동 retention 스케줄러 (E R3-Day 4).

FastAPI ``lifespan`` hook 에서 ``start_retention_loop`` 를 호출하면, 백그라운드
``asyncio.Task`` 가 ``interval_hours`` 마다 ``InboxService.purge_older_than`` 을
수행한다. 종료 시 ``stop_retention_loop`` 가 task 를 cancel.

설계:
    - **opt-in** — ``PushConfig.inbox_retention_enabled=False`` 면 noop.
    - **single-process** — 본 loop 는 *프로세스 내* 1 회 실행. 다중 워커
      (uvicorn --workers N) 에서는 N 회 동시 수행되므로 락이 필요 — 본 라운드는
      DB delete 가 idempotent 이므로 무방. R4 백로그: PostgreSQL advisory lock.
    - **session factory DI** — 호출자가 ``session_maker`` 를 넘기면 내부에서
      ``async with session_maker() as db: ...`` 형태로 fresh 세션을 사용.
    - **best-effort** — 한 cycle 실패가 다음 cycle 을 막지 않는다.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from .config import PushConfig
from .inbox_service import InboxService


log = logging.getLogger("notifications.retention")


SessionFactory = Callable[[], Awaitable[object]]
"""``async with session_factory() as db`` 형태의 컨텍스트 매니저 팩토리."""


async def run_retention_cycle(
    inbox: InboxService,
    config: PushConfig,
    session_maker,  # async_sessionmaker 또는 같은 인터페이스
) -> int:
    """한 사이클을 수행. 삭제된 row 수 반환 (또는 -1 실패)."""
    if not config.inbox_retention_enabled:
        return 0
    try:
        async with session_maker() as db:
            deleted = await inbox.purge_older_than(
                db,
                days=int(config.inbox_retention_days),
                include_unread=bool(config.inbox_retention_include_unread),
            )
            await db.commit()
        return deleted
    except Exception as exc:  # pragma: no cover - best-effort
        log.exception("retention_cycle_failed err=%s", exc)
        return -1


async def _loop(
    inbox: InboxService,
    config: PushConfig,
    session_maker,
    stop: asyncio.Event,
) -> None:
    interval_s = max(1, int(config.inbox_retention_interval_hours) * 3600)
    log.info(
        "inbox_retention_loop_started service=%s interval_h=%s days=%s include_unread=%s",
        inbox.service_name or "?", config.inbox_retention_interval_hours,
        config.inbox_retention_days, config.inbox_retention_include_unread,
    )
    while not stop.is_set():
        await run_retention_cycle(inbox, config, session_maker)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except asyncio.TimeoutError:
            pass


def start_retention_loop(
    inbox: InboxService,
    config: PushConfig,
    session_maker,
) -> tuple[asyncio.Task, asyncio.Event]:
    """백그라운드 task 시작. ``stop_retention_loop(task, event)`` 로 graceful stop.

    Returns:
        ``(task, stop_event)`` — caller (FastAPI lifespan) 가 들고 있다가 종료
        시 ``stop.set()`` + ``await task`` 로 정리.
    """
    stop = asyncio.Event()
    task = asyncio.create_task(_loop(inbox, config, session_maker, stop))
    return task, stop


async def stop_retention_loop(task: asyncio.Task, stop: asyncio.Event) -> None:
    """graceful shutdown — stop 시그널 set + task await (5s timeout)."""
    stop.set()
    try:
        await asyncio.wait_for(task, timeout=5.0)
    except asyncio.TimeoutError:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # pragma: no cover
            pass
    log.info("inbox_retention_loop_stopped")


__all__ = [
    "run_retention_cycle",
    "start_retention_loop",
    "stop_retention_loop",
]
