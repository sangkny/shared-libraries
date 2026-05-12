"""shared-libraries/saas — pure helpers 단위 테스트.

테스트 철학 (2026-05-12):
    - LLM/네트워크 mock 없음 — saas/helpers.py 는 순수 계산 함수
    - DB 의존 분기는 ADK / CoOps 의 통합 테스트에서 검증 (`tests/test_billing.py`)
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from saas.helpers import (
    DEFAULT_FREE_PLAN_CODE,
    current_year_month,
    next_month_reset_ts,
    parse_allowed_models,
    quota_headers,
    usage_snapshot_dict,
)


def test_default_free_plan_code_is_free() -> None:
    assert DEFAULT_FREE_PLAN_CODE == "free"


def test_current_year_month_format() -> None:
    fixed = datetime(2026, 5, 12, 4, 5, 6, tzinfo=timezone.utc)
    assert current_year_month(fixed) == "2026-05"


def test_current_year_month_january() -> None:
    fixed = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    assert current_year_month(fixed) == "2026-01"


def test_next_month_reset_ts_advances_to_first_of_next_month() -> None:
    fixed = datetime(2026, 5, 12, 4, 5, 6, tzinfo=timezone.utc)
    ts = next_month_reset_ts(fixed)
    expected = datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
    assert ts == int(expected.timestamp())


def test_next_month_reset_ts_december_wraps_to_january() -> None:
    fixed = datetime(2026, 12, 25, 10, 0, 0, tzinfo=timezone.utc)
    ts = next_month_reset_ts(fixed)
    expected = datetime(2027, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    assert ts == int(expected.timestamp())


def test_quota_headers_with_finite_quota() -> None:
    fixed = datetime(2026, 5, 12, 0, 0, 0, tzinfo=timezone.utc)
    reset = next_month_reset_ts(fixed)
    h = quota_headers(1000, 87, reset_ts=reset)
    assert h["X-RateLimit-Limit"] == "1000"
    assert h["X-RateLimit-Remaining"] == "913"
    assert h["X-RateLimit-Reset"] == str(reset)


def test_quota_headers_unlimited() -> None:
    h = quota_headers(None, 99999)
    assert h["X-RateLimit-Limit"] == "-1"
    assert h["X-RateLimit-Remaining"] == "-1"
    assert int(h["X-RateLimit-Reset"]) > 0


def test_quota_headers_remaining_clamps_at_zero() -> None:
    h = quota_headers(100, 150)
    assert h["X-RateLimit-Limit"] == "100"
    assert h["X-RateLimit-Remaining"] == "0"


def test_parse_allowed_models_single() -> None:
    assert parse_allowed_models("FAST") == ["FAST"]


def test_parse_allowed_models_csv_trims_spaces() -> None:
    assert parse_allowed_models("FAST, HEAVY ,CONSENSUS") == [
        "FAST",
        "HEAVY",
        "CONSENSUS",
    ]


def test_parse_allowed_models_empty_yields_empty_list() -> None:
    assert parse_allowed_models("") == []
    assert parse_allowed_models(None) == []  # type: ignore[arg-type]


class _DuckMonthly:
    def __init__(self, year_month: str, calls: int, tokens: int, cost: float) -> None:
        self.year_month = year_month
        self.calls_count = calls
        self.tokens_total = tokens
        self.cost_usd = cost


class _DuckPlan:
    def __init__(self, quota: int | None) -> None:
        self.monthly_call_quota = quota


def test_usage_snapshot_dict_with_finite_quota_calculates_pct() -> None:
    snap = usage_snapshot_dict(
        _DuckMonthly("2026-05", 100, 12000, 0.05),
        _DuckPlan(1000),
    )
    assert snap == {
        "year_month": "2026-05",
        "calls_used": 100,
        "calls_limit": 1000,
        "calls_remaining": 900,
        "quota_pct": 10.0,
        "tokens_total": 12000,
        "cost_usd": 0.05,
    }


def test_usage_snapshot_dict_unlimited_quota_returns_none_remaining() -> None:
    snap = usage_snapshot_dict(
        _DuckMonthly("2026-05", 50000, 0, 0),
        _DuckPlan(None),
    )
    assert snap["calls_limit"] is None
    assert snap["calls_remaining"] is None
    assert snap["quota_pct"] == 0.0


def test_usage_snapshot_dict_over_quota_clamps_remaining_to_zero() -> None:
    snap = usage_snapshot_dict(
        _DuckMonthly("2026-05", 150, 0, 0),
        _DuckPlan(100),
    )
    assert snap["calls_remaining"] == 0
    assert snap["quota_pct"] == 150.0  # 보고용 — 100% 초과 표시
