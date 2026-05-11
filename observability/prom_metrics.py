"""
Prometheus 텍스트 포맷 메트릭 — Step 4 (book §16.10.3 / §16.12.2).

설계 원칙
=========
- 메트릭 이름은 **prefix 없이 전역 단일** (`chunking_*`). 서비스 구분은 `service`
  라벨로 표현 — Prometheus 컨벤션. Grafana 에서 cross-service 비교가 깔끔하게 떨어진다.
- 라벨 카디널리티 통제:
    - `service` : 4종 (medi / coops / adk_architecture / adk_svg)
    - `flow`    : 4종 (= medi_diagnosis / coops_contract_analysis /
                       adk_architecture_decision / adk_svg_generation)
    - `strategy`: 4종 (pipeline / consensus / debate / fastest) — domain-free
    - `domain`  : 선택적 (medical / business / software / svg) — 비어 있을 수 있다
    - `model`   : **라벨 아님** (시계열 폭발 우려) — 별도 Info 메트릭으로
- ``shared-libraries/agents/context_chunking.py:chunking_metrics_snapshot()`` 의
  11종 dict 키를 1:1 로 Gauge 에 옮기고, 추가 카운터/히스토그램(`*_total`,
  `*_seconds`)을 사용자 제안 11종에서 4종(success/failed/overflow + duration_seconds)
  채택. 나머지 quality/preservation 은 Step 5 SLO 단계에서 정의 후 추가.

배포 방식
=========
- 기존 ``/metrics`` (JSON snapshot) 는 보존 — health-aggregator 등 기존 호출자의
  영향 0.
- 새 엔드포인트 ``/metrics/prometheus`` 로 텍스트 포맷 노출. Prometheus
  scrape config 의 ``metrics_path: /metrics/prometheus`` 로 지정한다.

사용
=====
- import-time 부작용 없음. 첫 ``observe_chunking_snapshot(...)`` 호출에서
  prometheus_client 가 글로벌 REGISTRY 에 메트릭을 등록한다 (멱등).
- 모듈 수준 instance lock 으로 중복 등록 방지 (uvicorn --reload 등에서 안전).
- prometheus_client 미설치 시 ``observe_chunking_snapshot`` 은 no-op 로
  graceful degrade. 기존 한 줄 로그(``medi_diagnosis_context`` 등) 거동은 변하지 않는다.
"""
from __future__ import annotations

import threading
from typing import Any, Mapping


try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        Counter,
        Gauge,
        Histogram,
        Info,
        generate_latest,
    )
    _HAS_PROM = True
except ImportError:  # graceful degrade
    _HAS_PROM = False
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"  # type: ignore[assignment]

    def generate_latest() -> bytes:  # type: ignore[misc]
        return b"# prometheus_client not installed\n"


# Prometheus scrape 시 4개 flow 의 chunking 라벨 키. 라벨 순서 고정.
_LABEL_NAMES_CHUNKING = ("service", "flow", "strategy", "domain")
_LABEL_NAMES_SERVICE_ONLY = ("service",)

# 등록 멱등 — uvicorn --reload, 테스트 재시작 시 ValueError("Duplicated timeseries") 방지.
_REGISTRY_LOCK = threading.Lock()
_REGISTERED: dict[str, Any] = {}


def _g(name: str, doc: str, labelnames: tuple[str, ...] = _LABEL_NAMES_CHUNKING):
    """Gauge 멱등 등록."""
    if not _HAS_PROM:
        return None
    with _REGISTRY_LOCK:
        if name in _REGISTERED:
            return _REGISTERED[name]
        m = Gauge(name, doc, labelnames=labelnames)
        _REGISTERED[name] = m
        return m


def _c(name: str, doc: str, labelnames: tuple[str, ...] = _LABEL_NAMES_CHUNKING):
    if not _HAS_PROM:
        return None
    with _REGISTRY_LOCK:
        if name in _REGISTERED:
            return _REGISTERED[name]
        m = Counter(name, doc, labelnames=labelnames)
        _REGISTERED[name] = m
        return m


def _h(
    name: str,
    doc: str,
    labelnames: tuple[str, ...] = _LABEL_NAMES_CHUNKING,
    buckets: tuple[float, ...] = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
):
    if not _HAS_PROM:
        return None
    with _REGISTRY_LOCK:
        if name in _REGISTERED:
            return _REGISTERED[name]
        m = Histogram(name, doc, labelnames=labelnames, buckets=buckets)
        _REGISTERED[name] = m
        return m


def _i(name: str, doc: str):
    if not _HAS_PROM:
        return None
    with _REGISTRY_LOCK:
        if name in _REGISTERED:
            return _REGISTERED[name]
        m = Info(name, doc)
        _REGISTERED[name] = m
        return m


# ─── chunking_metrics_snapshot 의 11종 키 → Gauge ────────────────────────────
def _ensure_metrics() -> None:
    """첫 observe 호출 시 모든 메트릭을 등록한다 (멱등)."""
    _g("chunking_model_context_window_tokens",
       "Model context window in tokens (resolved by model label).")
    _g("chunking_submission_budget_tokens",
       "Submission budget in tokens (after safety fraction).")
    _g("chunking_chunk_token_budget_tokens",
       "Per-chunk body token budget.")
    _g("chunking_input_tokens",
       "Body tokens estimated for the prompt (alias of body_tokens_estimated).")
    _g("chunking_total_tokens",
       "Total tokens estimated (body + fixed overhead).")
    _g("chunking_chunks_needed",
       "Recommended number of chunks based on analysis.")
    _g("chunking_chunks_produced",
       "Number of chunks actually produced.")
    _g("chunking_fits_context",
       "Whether the prompt fits a single call (1) or not (0).")
    _g("chunking_overlap_inflation_ratio",
       "(sum of chunk tokens) / body_tokens — 1.0+ when overlap is applied.")

    # 사용자 제안 보완 메트릭 (실행 결과)
    _g("chunking_compression_ratio",
       "Effective compression ratio (post-tokens / pre-tokens). Reviewer/orch.")
    _h("chunking_duration_seconds",
       "Time spent in chunking pipeline (analyze+split+merge), seconds.")
    _h("chunking_total_seconds",
       "Total wall-clock from chunking entry to LLM result, seconds.")

    _c("chunking_chunks_success_total",
       "Successful chunk runs.")
    _c("chunking_chunks_failed_total",
       "Failed chunk runs.")
    _c("chunking_context_overflow_total",
       "Context size exceeded occurrences (provider 400 or detected upstream).")
    _c("chunking_invocations_total",
       "Total invocations observed (every observe_chunking_snapshot call). "
       "Sanity counter for scrape verification.")

    _i("chunking_model_info",
       "Last model label seen per service (low-cardinality; do not use for alerts).")


def observe_chunking_snapshot(
    snapshot: Mapping[str, Any],
    *,
    service: str,
    flow: str,
    strategy: str = "",
    domain: str = "",
) -> None:
    """
    ``chunking_metrics_snapshot(analysis, chunks)`` 의 dict 를 받아 Prometheus
    Gauge 11종 + invocation Counter 1종에 set/inc. 비-숫자 키 (``chunking_model``,
    ``chunking_recommendation``) 는 메트릭이 아니라 Info 라벨로 옮긴다.

    Args:
        snapshot: ``chunking_metrics_snapshot()`` 결과 (또는 동등한 dict).
        service: 라벨 — 4종 중 하나 (예: ``"medi"``).
        flow: 라벨 — 4종 중 하나 (예: ``"medi_diagnosis"``).
        strategy: 라벨 — orchestrator strategy (소문자). 미지정 시 빈 문자열.
        domain: 라벨 — Ontology domain (소문자). 미지정 시 빈 문자열.
    """
    if not _HAS_PROM:
        return
    _ensure_metrics()
    labels = {
        "service": service,
        "flow": flow,
        "strategy": (strategy or "").lower(),
        "domain": (domain or "").lower(),
    }

    def _set(name: str, value: Any) -> None:
        m = _REGISTERED.get(name)
        if m is None or value is None:
            return
        try:
            m.labels(**labels).set(float(value))
        except Exception:  # 라벨 mismatch / 값 형변환 실패는 무시 (관측은 best-effort)
            pass

    _set("chunking_model_context_window_tokens", snapshot.get("chunking_model_context_window"))
    _set("chunking_submission_budget_tokens", snapshot.get("chunking_submission_budget"))
    _set("chunking_chunk_token_budget_tokens", snapshot.get("chunking_chunk_token_budget"))
    _set("chunking_input_tokens", snapshot.get("chunking_body_tokens_estimated"))
    _set("chunking_total_tokens", snapshot.get("chunking_total_tokens_estimated"))
    _set("chunking_chunks_needed", snapshot.get("chunking_chunks_needed"))
    _set("chunking_chunks_produced", snapshot.get("chunking_chunks_produced"))
    _set("chunking_fits_context", 1 if snapshot.get("chunking_fits_context") else 0)
    _set("chunking_overlap_inflation_ratio", snapshot.get("chunking_overlap_inflation_ratio"))

    inv = _REGISTERED.get("chunking_invocations_total")
    if inv is not None:
        try:
            inv.labels(**labels).inc()
        except Exception:
            pass

    # 모델 라벨은 Info 메트릭으로 (라벨이 아닌 정보 필드).
    model = snapshot.get("chunking_model")
    if model:
        info = _REGISTERED.get("chunking_model_info")
        if info is not None:
            try:
                info.info({"service": service, "flow": flow, "model": str(model)})
            except Exception:
                pass


def observe_chunking_compression(
    *,
    service: str,
    flow: str,
    pre_tokens: int,
    post_tokens: int,
    strategy: str = "",
    domain: str = "",
) -> None:
    """
    Reviewer trim 또는 Orchestrator prepare_orchestrator_context 적용 후의
    compression_ratio 를 기록 (post/pre, 0~1 범위).
    """
    if not _HAS_PROM:
        return
    _ensure_metrics()
    ratio = float(post_tokens) / max(1.0, float(pre_tokens))
    g = _REGISTERED.get("chunking_compression_ratio")
    if g is not None:
        try:
            g.labels(
                service=service, flow=flow,
                strategy=(strategy or "").lower(),
                domain=(domain or "").lower(),
            ).set(ratio)
        except Exception:
            pass


def observe_chunking_duration(
    *,
    service: str,
    flow: str,
    seconds: float,
    kind: str = "duration",
    strategy: str = "",
    domain: str = "",
) -> None:
    """
    Histogram observe. ``kind`` 가 ``"duration"`` 이면
    ``chunking_duration_seconds``, ``"total"`` 이면 ``chunking_total_seconds``.
    """
    if not _HAS_PROM:
        return
    _ensure_metrics()
    name = (
        "chunking_total_seconds" if kind == "total" else "chunking_duration_seconds"
    )
    h = _REGISTERED.get(name)
    if h is None:
        return
    try:
        h.labels(
            service=service, flow=flow,
            strategy=(strategy or "").lower(),
            domain=(domain or "").lower(),
        ).observe(max(0.0, float(seconds)))
    except Exception:
        pass


def inc_chunking_counter(
    *,
    service: str,
    flow: str,
    kind: str,
    amount: float = 1.0,
    strategy: str = "",
    domain: str = "",
) -> None:
    """
    ``kind`` ∈ {``"success"``, ``"failed"``, ``"overflow"``} — 각각
    ``chunking_chunks_success_total`` / ``chunking_chunks_failed_total`` /
    ``chunking_context_overflow_total`` 에 inc.
    """
    if not _HAS_PROM:
        return
    _ensure_metrics()
    name = {
        "success": "chunking_chunks_success_total",
        "failed": "chunking_chunks_failed_total",
        "overflow": "chunking_context_overflow_total",
    }.get(kind)
    if name is None:
        return
    c = _REGISTERED.get(name)
    if c is None:
        return
    try:
        c.labels(
            service=service, flow=flow,
            strategy=(strategy or "").lower(),
            domain=(domain or "").lower(),
        ).inc(amount)
    except Exception:
        pass


def render_prometheus_text() -> tuple[bytes, str]:
    """
    GET /metrics/prometheus 응답 본문 + Content-Type 을 돌려준다.

    Returns:
        ``(body_bytes, content_type)``. prometheus_client 미설치 시에도
        텍스트 형태의 안내 한 줄을 돌려줘 scrape 자체는 200 으로 유지.
    """
    if not _HAS_PROM:
        return (
            b"# prometheus_client not installed in this service - install "
            b"'prometheus-client>=0.20.0' to enable Prometheus metrics.\n",
            CONTENT_TYPE_LATEST,
        )
    _ensure_metrics()
    return generate_latest(), CONTENT_TYPE_LATEST


__all__ = [
    "CONTENT_TYPE_LATEST",
    "inc_chunking_counter",
    "observe_chunking_compression",
    "observe_chunking_duration",
    "observe_chunking_snapshot",
    "render_prometheus_text",
]
