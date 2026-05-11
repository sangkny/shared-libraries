"""observability.prom_metrics — Prometheus 텍스트 포맷 메트릭 단위 회귀.

prometheus_client 미설치 환경도 graceful degrade 함을 검증 (서비스 코드의 거동
영향 0). 설치된 환경에서는 4개 flow 의 ``chunking_*`` 표준 메트릭이 정확히
1세트로 노출되는지 확인한다.
"""
from __future__ import annotations

import pytest

from agents.context_chunking import (
    analyze_prompt_for_model,
    chunking_metrics_snapshot,
)
from observability import prom_metrics


# ───────────────────────────────────────────────────────────────────────────
# Skip 가드 — prometheus_client 미설치 시
# ───────────────────────────────────────────────────────────────────────────

prom_required = pytest.mark.skipif(
    not getattr(prom_metrics, "_HAS_PROM", False),
    reason="prometheus_client 미설치 — graceful degrade 경로만 검증한다",
)


# ───────────────────────────────────────────────────────────────────────────
# Helper — snapshot fixture
# ───────────────────────────────────────────────────────────────────────────


def _make_snapshot(prompt: str = "환자 검사 기록을 검토하고 진단 보고서를 작성하세요.") -> dict:
    analysis = analyze_prompt_for_model(prompt, model="google/gemma-4-26b-a4b")
    return chunking_metrics_snapshot(
        analysis,
        [],
        extra={
            "flow": "medi_diagnosis",
            "exam_id": "TEST-EXAM-001",
            "strategy": "consensus",
            "rag_used": False,
        },
    )


# ───────────────────────────────────────────────────────────────────────────
# Graceful degrade
# ───────────────────────────────────────────────────────────────────────────


def test_render_returns_content_type_even_when_prom_missing() -> None:
    body, content_type = prom_metrics.render_prometheus_text()
    assert isinstance(body, (bytes, bytearray))
    assert content_type.startswith("text/plain")


def test_observe_is_noop_when_prom_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prom_metrics, "_HAS_PROM", False)
    snap = _make_snapshot()
    prom_metrics.observe_chunking_snapshot(
        snap, service="medi", flow="medi_diagnosis", strategy="consensus", domain="medical"
    )
    prom_metrics.inc_chunking_counter(
        service="medi", flow="medi_diagnosis", kind="overflow"
    )
    prom_metrics.observe_chunking_duration(
        service="medi", flow="medi_diagnosis", seconds=0.5, kind="duration"
    )
    body, _ = prom_metrics.render_prometheus_text()
    assert b"prometheus_client not installed" in body


# ───────────────────────────────────────────────────────────────────────────
# Standard exposure
# ───────────────────────────────────────────────────────────────────────────


@prom_required
def test_observe_snapshot_emits_gauges_for_all_11_keys() -> None:
    snap = _make_snapshot()
    prom_metrics.observe_chunking_snapshot(
        snap, service="medi", flow="medi_diagnosis",
        strategy="consensus", domain="medical",
    )
    body, content_type = prom_metrics.render_prometheus_text()
    text = body.decode("utf-8", errors="replace")
    assert "text/plain" in content_type
    # prometheus_client 0.20+ 는 version=0.0.4 (legacy) 또는 1.0.0 (openmetrics)
    assert "version=" in content_type

    expected_metric_names = (
        "chunking_model_context_window_tokens",
        "chunking_submission_budget_tokens",
        "chunking_chunk_token_budget_tokens",
        "chunking_input_tokens",
        "chunking_total_tokens",
        "chunking_chunks_needed",
        "chunking_chunks_produced",
        "chunking_fits_context",
        "chunking_overlap_inflation_ratio",
        "chunking_invocations_total",
    )
    for name in expected_metric_names:
        assert name in text, f"missing metric: {name}"
    assert "chunking_model_info" in text
    assert 'service="medi"' in text
    assert 'flow="medi_diagnosis"' in text
    assert 'strategy="consensus"' in text
    assert 'domain="medical"' in text


@prom_required
def test_observe_snapshot_repeated_does_not_duplicate_series() -> None:
    snap = _make_snapshot()
    for _ in range(3):
        prom_metrics.observe_chunking_snapshot(
            snap, service="medi", flow="medi_diagnosis",
            strategy="consensus", domain="medical",
        )
    body, _ = prom_metrics.render_prometheus_text()
    text = body.decode("utf-8", errors="replace")
    occurrences = text.count(
        'chunking_chunks_needed{domain="medical",flow="medi_diagnosis",service="medi",strategy="consensus"}'
    )
    assert occurrences == 1, "Same labelset must not duplicate (HELP/TYPE separate)"


@prom_required
def test_inc_counters_increment_only_target_kind() -> None:
    prom_metrics.inc_chunking_counter(
        service="coops", flow="coops_contract_analysis",
        kind="overflow", strategy="debate", domain="business",
    )
    prom_metrics.inc_chunking_counter(
        service="coops", flow="coops_contract_analysis",
        kind="success", strategy="debate", domain="business",
    )
    prom_metrics.inc_chunking_counter(
        service="coops", flow="coops_contract_analysis",
        kind="success", strategy="debate", domain="business",
    )
    body, _ = prom_metrics.render_prometheus_text()
    text = body.decode("utf-8", errors="replace")
    label_block = (
        'domain="business",flow="coops_contract_analysis",service="coops",strategy="debate"'
    )
    assert f'chunking_context_overflow_total{{{label_block}}} 1.0' in text
    assert f'chunking_chunks_success_total{{{label_block}}} 2.0' in text


@prom_required
def test_duration_histogram_records_buckets() -> None:
    prom_metrics.observe_chunking_duration(
        service="adk_arch", flow="adk_architecture_decision",
        seconds=0.4, kind="duration", strategy="debate", domain="software",
    )
    prom_metrics.observe_chunking_duration(
        service="adk_arch", flow="adk_architecture_decision",
        seconds=3.0, kind="total", strategy="debate", domain="software",
    )
    body, _ = prom_metrics.render_prometheus_text()
    text = body.decode("utf-8", errors="replace")
    assert "chunking_duration_seconds_bucket" in text
    assert "chunking_total_seconds_bucket" in text
    assert 'service="adk_arch"' in text


@prom_required
def test_compression_ratio_records_post_over_pre() -> None:
    prom_metrics.observe_chunking_compression(
        service="medi", flow="medi_diagnosis",
        pre_tokens=1200, post_tokens=400,
        strategy="consensus", domain="medical",
    )
    body, _ = prom_metrics.render_prometheus_text()
    text = body.decode("utf-8", errors="replace")
    label_block = (
        'domain="medical",flow="medi_diagnosis",service="medi",strategy="consensus"'
    )
    assert f'chunking_compression_ratio{{{label_block}}}' in text
    line = next(
        (
            l for l in text.splitlines()
            if l.startswith("chunking_compression_ratio")
            and label_block in l
        ),
        "",
    )
    value = float(line.rsplit(" ", 1)[-1])
    assert abs(value - (400 / 1200)) < 1e-6


@prom_required
def test_observe_handles_missing_keys_gracefully() -> None:
    minimal_snapshot: dict = {
        "chunking_model": "google/gemma-4-e4b",
        "chunking_chunks_needed": 2,
    }
    prom_metrics.observe_chunking_snapshot(
        minimal_snapshot,
        service="adk_svg", flow="adk_svg_generation",
        strategy="", domain="svg",
    )
    body, _ = prom_metrics.render_prometheus_text()
    text = body.decode("utf-8", errors="replace")
    assert 'service="adk_svg"' in text


@prom_required
def test_render_prometheus_text_is_idempotent_format() -> None:
    body_a, _ = prom_metrics.render_prometheus_text()
    body_b, _ = prom_metrics.render_prometheus_text()
    text_a = body_a.decode("utf-8", errors="replace")
    text_b = body_b.decode("utf-8", errors="replace")
    for name in (
        "# TYPE chunking_chunks_needed gauge",
        "# TYPE chunking_chunks_success_total counter",
        "# TYPE chunking_duration_seconds histogram",
    ):
        assert text_a.count(name) == 1
        assert text_b.count(name) == 1
