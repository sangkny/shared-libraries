"""SaaS billing/quota 도메인 Prometheus 메트릭 (C Week 3 Day 3, 2026-05-12).

기존 ``observability/prom_metrics.py`` 의 chunking 메트릭과 함께 같은 글로벌
``REGISTRY`` 에 등록되어, ``GET /metrics/prometheus`` 에서 함께 노출된다.

설계 원칙
=========
- **prefix 없는 전역 단일** — 메트릭명은 ``saas_*`` 로 시작. ``service`` 라벨로
  ADK / CoOps / 향후 MEDI 구분 (chunking 과 동일 컨벤션).
- **라벨 카디널리티 통제** — ``plan_code`` 는 고정 4종 (free/startup/smb/ent 또는
  free/dev/team/ent), ``action`` 은 라우트별 enum, ``status`` 는 ``allowed`` /
  ``blocked`` 2종.
- **카운터·게이지 분리** — 누적 카운터는 ``*_total``, 현재 스냅샷은 게이지.
- **prometheus_client 미설치 시 graceful degrade** — 모든 helper 는 no-op.
- **import-time 부작용 없음** — 첫 호출에서 멱등 등록 (uvicorn --reload 안전).

엔드포인트
==========
``/metrics/prometheus`` (이미 ``shared.observability.install_observability`` 가
ADK/CoOps 의 ``main.py`` 에 mount). 본 모듈을 import 한 뒤 helper 를 호출하면
자동 노출.
"""
from __future__ import annotations

import threading
from typing import Any

try:
    from prometheus_client import Counter, Gauge

    _HAS_PROM = True
except ImportError:  # graceful degrade
    _HAS_PROM = False


_REGISTRY_LOCK = threading.Lock()
_REGISTERED: dict[str, Any] = {}

# 라벨 컨벤션
_LABELS_CALL = ("service", "plan_code", "action", "status")
_LABELS_PLAN = ("service", "plan_code")
_LABELS_REVENUE = ("service",)


def _gauge(name: str, doc: str, labels: tuple[str, ...]) -> Any:
    if not _HAS_PROM:
        return None
    with _REGISTRY_LOCK:
        if name in _REGISTERED:
            return _REGISTERED[name]
        m = Gauge(name, doc, labelnames=labels)
        _REGISTERED[name] = m
        return m


def _counter(name: str, doc: str, labels: tuple[str, ...]) -> Any:
    if not _HAS_PROM:
        return None
    with _REGISTRY_LOCK:
        if name in _REGISTERED:
            return _REGISTERED[name]
        m = Counter(name, doc, labelnames=labels)
        _REGISTERED[name] = m
        return m


def _ensure_metrics() -> None:
    """첫 호출 시 4종 카운터 + 2종 게이지 등록."""
    _counter(
        "saas_calls_total",
        "Total SaaS billable calls (record_call() invocations).",
        _LABELS_CALL,
    )
    _counter(
        "saas_quota_blocked_total",
        "Calls blocked at enforce_quota with HTTP 429.",
        ("service", "plan_code", "action"),
    )
    _counter(
        "saas_plan_transitions_total",
        "Plan transitions (admin subscribe / Stripe webhook).",
        ("service", "from_plan", "to_plan", "channel"),
    )
    _counter(
        "saas_stripe_webhook_events_total",
        "Stripe webhook events received and dispatched.",
        ("service", "event_type", "action"),
    )

    _gauge(
        "saas_active_subscribers",
        "Currently active subscribers per plan (snapshot).",
        _LABELS_PLAN,
    )
    _gauge(
        "saas_monthly_revenue_usd",
        "Aggregate monthly revenue USD across all paid plans (snapshot).",
        _LABELS_REVENUE,
    )


def inc_saas_call(
    *,
    service: str,
    plan_code: str,
    action: str,
    success: bool,
) -> None:
    """``record_call`` 호출 직후 카운터 +1.

    ``success=False`` (실패) 면 status=``failed``, 성공이면 ``allowed``.
    quota 차단 (429) 은 별도 ``inc_saas_quota_blocked`` 를 호출 — 본 함수는
    ``allowed``/``failed`` 만 다룬다.
    """
    if not _HAS_PROM:
        return
    _ensure_metrics()
    c = _REGISTERED.get("saas_calls_total")
    if c is None:
        return
    try:
        c.labels(
            service=service,
            plan_code=plan_code,
            action=action,
            status="allowed" if success else "failed",
        ).inc()
    except Exception:
        pass


def inc_saas_quota_blocked(
    *, service: str, plan_code: str, action: str
) -> None:
    """``enforce_quota`` 가 429 를 반환할 때 +1."""
    if not _HAS_PROM:
        return
    _ensure_metrics()
    c = _REGISTERED.get("saas_quota_blocked_total")
    if c is None:
        return
    try:
        c.labels(service=service, plan_code=plan_code, action=action).inc()
    except Exception:
        pass


def inc_saas_plan_transition(
    *,
    service: str,
    from_plan: str,
    to_plan: str,
    channel: str = "admin",
) -> None:
    """``switch_subscription`` 성공 시 +1.

    ``channel`` ∈ {``"admin"``, ``"stripe"``} — admin /subscribe 수기 부여 vs.
    Stripe webhook 자동 전환 구분.
    """
    if not _HAS_PROM:
        return
    _ensure_metrics()
    c = _REGISTERED.get("saas_plan_transitions_total")
    if c is None:
        return
    try:
        c.labels(
            service=service,
            from_plan=from_plan or "none",
            to_plan=to_plan,
            channel=channel,
        ).inc()
    except Exception:
        pass


def inc_saas_stripe_webhook(
    *, service: str, event_type: str, action: str
) -> None:
    """Stripe ``handle_event`` 의 ``{type, action}`` 결과를 +1.

    ``action`` ∈ {switched, updated, canceled, ignored, missing_fields, ...}.
    """
    if not _HAS_PROM:
        return
    _ensure_metrics()
    c = _REGISTERED.get("saas_stripe_webhook_events_total")
    if c is None:
        return
    try:
        c.labels(service=service, event_type=event_type, action=action).inc()
    except Exception:
        pass


def set_saas_active_subscribers(
    *, service: str, plan_code: str, count: int
) -> None:
    """주기적인 admin/stats scrape 또는 백그라운드 잡이 갱신."""
    if not _HAS_PROM:
        return
    _ensure_metrics()
    g = _REGISTERED.get("saas_active_subscribers")
    if g is None:
        return
    try:
        g.labels(service=service, plan_code=plan_code).set(float(count))
    except Exception:
        pass


def set_saas_monthly_revenue(*, service: str, revenue_usd: float) -> None:
    """월 매출 (USD) 스냅샷 — admin/stats 와 정합."""
    if not _HAS_PROM:
        return
    _ensure_metrics()
    g = _REGISTERED.get("saas_monthly_revenue_usd")
    if g is None:
        return
    try:
        g.labels(service=service).set(float(revenue_usd))
    except Exception:
        pass


__all__ = [
    "inc_saas_call",
    "inc_saas_quota_blocked",
    "inc_saas_plan_transition",
    "inc_saas_stripe_webhook",
    "set_saas_active_subscribers",
    "set_saas_monthly_revenue",
]
