"""
Redis Pub/Sub 기반 플랫폼 간 이벤트 버스.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Mapping

import redis.asyncio as redis

from .constants import DEFAULT_EVENTS_CHANNEL

log = logging.getLogger("events.bus")

AsyncEventHandler = Callable[[str, dict[str, Any]], Awaitable[None]]


class EventBus:
    """Redis 채널에 JSON 페이로드 publish / subscribe."""

    def __init__(self, redis_url: str) -> None:
        self.redis_url = redis_url

    async def publish(
        self,
        channel: str | None,
        event_type: str,
        data: Mapping[str, Any] | None = None,
    ) -> int:
        """
        Returns:
            해당 채널을 구독 중인 클라이언트 수 (Redis publish 반환값).
        """
        ch = channel or DEFAULT_EVENTS_CHANNEL
        payload_obj = {"event_type": event_type, "data": dict(data or {})}
        payload = json.dumps(payload_obj, ensure_ascii=False, default=str)
        client = redis.from_url(self.redis_url, decode_responses=True)
        try:
            n = await client.publish(ch, payload)
            log.debug("published channel=%s type=%s receivers=%s", ch, event_type, n)
            return int(n)
        finally:
            await client.aclose()

    async def subscribe(
        self,
        channel: str,
        handler: AsyncEventHandler,
        *,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        """
        메시지 루프 (블로킹). stop_event 가 set 되면 종료.

        Raises:
            기본적으로 핸들러 예외를 삼키고 로깅만 함 (브로커 루프 유지).
        """
        stop = stop_event if stop_event is not None else asyncio.Event()
        client = redis.from_url(self.redis_url, decode_responses=True)
        pubsub = client.pubsub()
        await pubsub.subscribe(channel)
        log.info("Redis subscribe 시작 channel=%s", channel)

        try:
            while not stop.is_set():
                raw = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=1.0,
                )
                if raw is None or raw["type"] != "message":
                    continue
                try:
                    body = json.loads(raw["data"])
                    et = str(body.get("event_type", "")).strip()
                    row = body.get("data")
                    pdata: dict[str, Any] = (
                        dict(row) if isinstance(row, dict) else {}
                    )
                    await handler(et, pdata)
                except json.JSONDecodeError:
                    log.warning("무시: JSON 파싱 실패 channel=%s", channel)
                except Exception:
                    log.exception("이벤트 핸들러 실패 channel=%s", channel)
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
            await client.aclose()
            log.info("Redis subscribe 종료 channel=%s", channel)


async def publish_platform_event(
    redis_url: str,
    event_type: str,
    data: Mapping[str, Any] | None = None,
    *,
    channel: str | None = None,
) -> int:
    """단발성 publish 헬퍼 (API 핸들러에서 호출)."""
    bus = EventBus(redis_url)
    return await bus.publish(channel, event_type, data)
