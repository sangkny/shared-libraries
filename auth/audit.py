"""Ontology 연동 감사 로그·PII 이상 징후 — Phase 2 Week 11."""
from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from events.constants import EVENT_SECURITY_PII_SUSPICIOUS

log = logging.getLogger("auth.audit")

# 간단한 PII/식별자 패턴 (이상 징후 카운트용)
_SSN_LIKE = re.compile(
    r"\b\d{3}[- ]?\d{2}[- ]?\d{4}\b|\b\d{6}[- ]?\d{7}\b",
)
_NAME_KEYS = ("주민", "ssn", "social security", "resident", "sin", "이름")

# 사용자별 SSN/식별자 유사 시도 횟수 (프로세스 로컬; 멀티 워커에서는 Redis 권장)
_pii_hits: dict[str, int] = {}
_WARN_AFTER = 3


def reset_pii_counters_for_tests() -> None:
    _pii_hits.clear()


def _flatten_ontology_payload(obj: Any) -> str:
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return str(obj)


def _count_pii_signals(text: str) -> int:
    t = text.lower()
    n = 0
    if _SSN_LIKE.search(text):
        n += 1
    for k in _NAME_KEYS:
        if k.lower() in t:
            n += 1
    return n


def _publish_sync(redis_url: str, event_type: str, data: dict[str, Any]) -> None:
    try:
        import redis

        from events.constants import DEFAULT_EVENTS_CHANNEL

        r = redis.from_url(redis_url, decode_responses=True)
        try:
            payload = json.dumps(
                {"event_type": event_type, "data": data},
                ensure_ascii=False,
                default=str,
            )
            r.publish(DEFAULT_EVENTS_CHANNEL, payload)
        finally:
            r.close()
    except Exception as e:
        log.warning("Redis 경고 이벤트 발행 실패: %s", e)


def log_with_ontology(
    user_id: str,
    endpoint: str,
    ontology_result: Any,
    *,
    redis_url: str | None = None,
) -> None:
    """
    API 경로와 Ontology 결과를 감사 로그로 남기고,
    본문에 SSN 유사 패턴이 반복되면 Redis Pub/Sub 경고를 보냅니다.
    """
    flat = _flatten_ontology_payload(ontology_result)
    passed = True
    if isinstance(ontology_result, dict):
        p = ontology_result.get("passed")
        if p is not None:
            passed = bool(p)
        op = ontology_result.get("ontology_passed")
        if op is not None:
            passed = bool(op) and passed

    sig = _count_pii_signals(flat)

    log.info(
        "audit ontology user=%s endpoint=%s passed=%s pii_signal=%s",
        user_id[:64],
        endpoint,
        passed,
        sig,
    )

    uid = user_id or "anonymous"
    suspicious = bool(_SSN_LIKE.search(flat))
    if suspicious:
        _pii_hits[uid] = _pii_hits.get(uid, 0) + 1
    cnt = _pii_hits.get(uid, 0)

    if suspicious:
        log.warning(
            "SSN-like pattern in ontology payload user=%s count=%s endpoint=%s",
            uid[:64],
            cnt,
            endpoint,
        )

    ru = (redis_url or "").strip()
    if ru and cnt >= _WARN_AFTER and suspicious:
        _publish_sync(
            ru,
            EVENT_SECURITY_PII_SUSPICIOUS,
            {
                "user_id": uid,
                "endpoint": endpoint,
                "attempt_count": cnt,
                "event_id": str(uuid.uuid4()),
                "ontology_passed": passed,
            },
        )
