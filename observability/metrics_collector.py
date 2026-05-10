"""
프로세스 로컬 메트릭 (LLM 호출, 토큰, 지연, HTTP 요약).
운영에서는 Prometheus sidecar·OTel 로 대체 가능.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

# 대략적 USD / 1M tokens (요금 변동 시 ENV로 덮어쓸 것)
DEFAULT_COST_PER_1M_IN: dict[str, float] = {
    "local":   0.0,
    "openai":  5.0,
    "anthropic": 3.0,
    "google":  1.25,
    "azure":   5.0,
}
DEFAULT_COST_PER_1M_OUT: dict[str, float] = {
    "local":   0.0,
    "openai":  15.0,
    "anthropic": 15.0,
    "google":  5.0,
    "azure":   15.0,
}

_lock = threading.Lock()


@dataclass
class _DayBucket:
    day_utc: date
    http_requests: int = 0
    http_ms_total: float = 0.0
    llm_chat_calls: int = 0
    llm_embed_calls: int = 0
    llm_tokens_in: int = 0
    llm_tokens_out: int = 0
    llm_ms_total: float = 0.0
    llm_calls_by_provider: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    llm_tokens_by_provider: dict[str, dict[str, int]] = field(default_factory=dict)


_bucket = _DayBucket(day_utc=datetime.now(timezone.utc).date())


def _rollover_if_needed() -> None:
    global _bucket
    today = datetime.now(timezone.utc).date()
    if _bucket.day_utc != today:
        _bucket = _DayBucket(day_utc=today)


def record_http_request(duration_ms: float) -> None:
    with _lock:
        _rollover_if_needed()
        _bucket.http_requests += 1
        _bucket.http_ms_total += duration_ms


def record_chat_response(response: Any) -> None:
    """LLMResponse — tokens / latency / provider."""
    try:
        provider = getattr(getattr(response, "provider", None), "value", None) or str(
            getattr(response, "provider", "unknown"),
        )
        in_tok = int(getattr(response, "input_tokens", 0) or 0)
        out_tok = int(getattr(response, "output_tokens", 0) or 0)
        lat = float(getattr(response, "latency_ms", 0) or 0.0)
    except Exception:
        return
    with _lock:
        _rollover_if_needed()
        _bucket.llm_chat_calls += 1
        _bucket.llm_tokens_in += in_tok
        _bucket.llm_tokens_out += out_tok
        _bucket.llm_ms_total += lat
        _bucket.llm_calls_by_provider[str(provider)] += 1
        d = _bucket.llm_tokens_by_provider.setdefault(
            str(provider),
            {"in": 0, "out": 0},
        )
        d["in"] += in_tok
        d["out"] += out_tok


def record_embed_call(
    provider_name: str,
    latency_ms: float,
    *,
    token_estimate: int = 0,
) -> None:
    with _lock:
        _rollover_if_needed()
        _bucket.llm_embed_calls += 1
        _bucket.llm_ms_total += latency_ms
        _bucket.llm_tokens_in += max(0, token_estimate)
        _bucket.llm_calls_by_provider[provider_name] += 1


def _cost_estimate_usd() -> float:
    total = 0.0
    for prov, counts in (_bucket.llm_tokens_by_provider or {}).items():
        pin = DEFAULT_COST_PER_1M_IN.get(prov, 2.0) / 1_000_000
        pout = DEFAULT_COST_PER_1M_OUT.get(prov, 6.0) / 1_000_000
        total += counts.get("in", 0) * pin + counts.get("out", 0) * pout
    return round(total, 6)


def snapshot(service_name: str) -> dict[str, Any]:
    with _lock:
        _rollover_if_needed()
        b = _bucket
        http_avg = (
            b.http_ms_total / b.http_requests if b.http_requests else 0.0
        )
        llm_denom = b.llm_chat_calls + b.llm_embed_calls
        llm_avg = b.llm_ms_total / llm_denom if llm_denom else 0.0
        return {
            "service":                    service_name,
            "date_utc":                 b.day_utc.isoformat(),
            "generated_at":             datetime.now(timezone.utc).isoformat(),
            "http_requests_total":      b.http_requests,
            "http_request_duration_ms_avg": round(http_avg, 3),
            "llm_calls_total":          b.llm_chat_calls + b.llm_embed_calls,
            "llm_chat_calls":           b.llm_chat_calls,
            "llm_embed_calls":          b.llm_embed_calls,
            "llm_tokens_input":         b.llm_tokens_in,
            "llm_tokens_output":        b.llm_tokens_out,
            "llm_latency_ms_avg":       round(llm_avg, 3),
            "llm_calls_by_provider":    dict(b.llm_calls_by_provider),
            "llm_tokens_by_provider":   dict(b.llm_tokens_by_provider),
            "estimated_cost_usd_today": _cost_estimate_usd(),
        }
