"""
Redis Pub/Sub 이벤트 버스 테스트 (Docker + redis 컨테이너 필요).

실행:
    docker compose -f docker-compose.dev.yml exec shared-libs \\
        pytest tests/test_events.py -v -s
"""
from __future__ import annotations

import asyncio
import os

import pytest
import redis.asyncio as redis

from events import (
    DEFAULT_EVENTS_CHANNEL,
    EVENT_CODE_GENERATED,
    EventBus,
)


def _redis_url() -> str:
    return (
        os.getenv("REDIS_URL")
        or os.getenv("TESTS_REDIS_URL")
        or ""
    )


@pytest.fixture
def redis_available() -> str:
    """Redis 연결 가능할 때만 URL 반환, 아니면 skip."""
    url = _redis_url().strip()
    if not url:
        pytest.skip("REDIS_URL 없음")

    async def _ping() -> None:
        client = redis.from_url(url, decode_responses=True)
        try:
            ok = await client.ping()
            if not ok:
                pytest.skip("Redis PING 실패")
        except (OSError, redis.ConnectionError) as e:
            pytest.skip(f"Redis 미사용 또는 연결 불가: {e}")
        finally:
            await client.aclose()

    asyncio.run(_ping())
    return url


@pytest.mark.asyncio
async def test_event_bus_publish_subscribe_roundtrip(redis_available: str) -> None:
    """publish → 같은 채널 구독 핸들러가 페이로드 수신."""

    loop = asyncio.get_running_loop()
    received: asyncio.Future[dict[str, object]] = loop.create_future()
    url = redis_available
    bus = EventBus(url)

    async def handler(event_type: str, data: dict[str, object]) -> None:
        if event_type == EVENT_CODE_GENERATED and not received.done():
            received.set_result(data)

    stop = asyncio.Event()
    task = asyncio.create_task(
        bus.subscribe(DEFAULT_EVENTS_CHANNEL, handler, stop_event=stop),
    )

    # 구독자가 채널에 올라갈 시간
    await asyncio.sleep(0.4)

    n = await bus.publish(
        DEFAULT_EVENTS_CHANNEL,
        EVENT_CODE_GENERATED,
        {"task_id": "t-week6", "lines": 42},
    )
    assert n >= 1, "발행했으나 구독자 없음(Redis 채널·타이밍 확인)"

    out = await asyncio.wait_for(received, timeout=5.0)
    assert out["task_id"] == "t-week6"
    assert out["lines"] == 42

    stop.set()
    await asyncio.wait_for(task, timeout=5.0)


@pytest.mark.asyncio
async def test_event_bus_payload_json_stable(redis_available: str) -> None:
    """이벤트 타입 문자열 매칭 검증."""

    loop = asyncio.get_running_loop()
    done = loop.create_future()
    bus = EventBus(redis_available)

    async def h(et: str, _: dict[str, object]) -> None:
        if et == EVENT_CODE_GENERATED and not done.done():
            done.set_result(True)

    stop = asyncio.Event()
    t = asyncio.create_task(
        bus.subscribe(DEFAULT_EVENTS_CHANNEL, h, stop_event=stop),
    )
    await asyncio.sleep(0.4)
    await bus.publish(DEFAULT_EVENTS_CHANNEL, EVENT_CODE_GENERATED, {})
    assert await asyncio.wait_for(done, timeout=5.0)

    stop.set()
    await asyncio.wait_for(t, timeout=5.0)
