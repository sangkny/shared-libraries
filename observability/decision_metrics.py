"""4-에이전트 결정 Prometheus 메트릭 — Grafana decision-audit 대시보드 연동."""
from __future__ import annotations

import threading
from typing import Any

try:
    from prometheus_client import Counter, Histogram

    _HAS_PROM = True
except ImportError:
    _HAS_PROM = False

_LOCK = threading.Lock()
_REGISTERED: dict[str, Any] = {}


def _counter(name: str, doc: str, labels: tuple[str, ...]) -> Any:
    if not _HAS_PROM:
        return None
    with _LOCK:
        if name in _REGISTERED:
            return _REGISTERED[name]
        m = Counter(name, doc, labelnames=labels)
        _REGISTERED[name] = m
        return m


def _histogram(name: str, doc: str, labels: tuple[str, ...]) -> Any:
    if not _HAS_PROM:
        return None
    with _LOCK:
        if name in _REGISTERED:
            return _REGISTERED[name]
        m = Histogram(
            name,
            doc,
            labelnames=labels,
            buckets=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
        )
        _REGISTERED[name] = m
        return m


def _ensure() -> None:
    _counter(
        "decision_total",
        "Agent decision count by decision, domain, mode.",
        ("decision", "domain", "mode"),
    )
    _histogram(
        "decision_score",
        "Agent decision score distribution.",
        ("domain", "score_type"),
    )
    _counter(
        "ontology_violations_total",
        "Ontology issues recorded at decision time.",
        ("domain", "issue"),
    )


def record_decision(
    *,
    decision: str,
    domain: str,
    mode: str,
    advocate: float = 0.0,
    critic: float = 0.0,
    final: float = 0.0,
    ontology_issues: list[str] | None = None,
) -> None:
    """결정 1건 메트릭 기록 (best-effort)."""
    if not _HAS_PROM:
        return
    _ensure()
    dec = (decision or "UNKNOWN").upper()
    dom = (domain or "general").lower()
    mmode = (mode or "legacy").lower()
    c = _REGISTERED.get("decision_total")
    if c is not None:
        c.labels(decision=dec, domain=dom, mode=mmode).inc()
    h = _REGISTERED.get("decision_score")
    if h is not None:
        if advocate > 0:
            h.labels(domain=dom, score_type="advocate").observe(advocate)
        if critic > 0:
            h.labels(domain=dom, score_type="critic").observe(critic)
        if final > 0:
            h.labels(domain=dom, score_type="final").observe(final)
    ov = _REGISTERED.get("ontology_violations_total")
    if ov is not None and ontology_issues:
        for issue in ontology_issues[:8]:
            key = (issue or "unknown")[:80]
            ov.labels(domain=dom, issue=key).inc()
