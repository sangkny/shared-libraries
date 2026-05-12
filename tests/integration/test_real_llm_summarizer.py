"""LMStudioJSONSummarizer 실 호출 통합 테스트 — Step 8 (2026-05-12).

CURSOR_HANDOVER §"테스트 철학" 원칙 100% 준수:
    - Mock 사용 0건 — 실 LM Studio /v1/chat/completions 호출
    - 토크나이저는 실 tiktoken (또는 estimate_tokens_multilingual fallback)
    - ``../conftest.py`` 의 ``is_lm_studio_ready()`` 게이트로 CI 자동 skip

검증 시나리오 (Step 6·7 의 mock 테스트를 대체):
    1. 정상 한국어 입력 → 비어있지 않은 응답 + last_retry_count ∈ {0, 1}
    2. ``max_tokens=1`` 극단 budget → 빈응답 또는 짧은 응답, retry 발동 가능성
    3. ``trim_text_with_llm_summary`` env ON + 패딩 입력 → LLM 요약 발동
       (실제 e4b 응답이 비어 있으면 ``llm_summary_error="empty_response"``)
    4. 잘못된 base_url → graceful fallback (결정적 trim 결과 유지)
"""
from __future__ import annotations

import asyncio
import os

import pytest

from agents.context_chunking import (
    LMStudioJSONSummarizer,
    trim_text_with_llm_summary,
)


pytestmark = pytest.mark.requires_lm_studio


def _summarizer(*, base_url: str | None = None, timeout: float = 30.0) -> LMStudioJSONSummarizer:
    return LMStudioJSONSummarizer(
        base_url=base_url
        or os.getenv("LM_STUDIO_BASE_URL", "http://host.docker.internal:8000/v1"),
        timeout=timeout,
    )


def test_real_lm_studio_summarizer_responds_to_korean_input() -> None:
    """정상 한국어 의료 입력 → 비어있지 않은 요약 + retry 발동 여부 관측.

    모델 응답 분포에 따라:
        - 정상: ``out`` 비어있지 않음, ``last_retry_count == 0``
        - e4b 빈응답 케이스: retry 후 ``last_retry_count == 1``,
          ``out`` 은 retry 결과 (비거나 짧을 수 있음)
    어느 쪽이든 ``str`` 반환 + retry_count 가 0/1 이어야 한다.
    """
    summarizer = _summarizer()
    text = (
        "안압 21mmHg, 망막 검사 상 미세혈관류 다수 관찰. 당뇨망막증 의심됨. "
        "환자 박씨 65세, 당뇨 12년차, HbA1c 8.2%."
    )

    out = asyncio.run(
        summarizer.summarize(text=text, max_tokens=128, hint="의료 진단 요약")
    )

    assert isinstance(out, str)
    assert summarizer.last_retry_count in (0, 1)
    if out.strip():
        assert len(out.strip()) >= 3


def test_real_lm_studio_summarizer_extreme_budget_returns_str() -> None:
    """``max_tokens=1`` 극단 budget → 빈응답/짧은응답 모두 graceful 처리.

    e4b 모델의 알려진 빈응답 케이스를 실 호출로 재현하기 위한 유도책.
    ``str`` 반환 + ``last_retry_count`` 가 0 또는 1 이어야 한다.
    """
    summarizer = _summarizer()
    out = asyncio.run(
        summarizer.summarize(
            text="당뇨 환자 박씨, 망막 검사 결과 미세혈관류 다수 관찰.",
            max_tokens=1,
            hint=None,
        )
    )
    assert isinstance(out, str)
    assert summarizer.last_retry_count in (0, 1)


def test_real_trim_text_with_llm_summary_padding_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """env ON + 패딩 입력 (chars fallback 유도) → LLM 요약 발동.

    실 LM Studio 응답에 따라 두 경로 모두 허용:
        - 응답 있음: ``fallback == "llm_summary"``, ``llm_summary_used`` True
        - 빈응답 (retry 후에도): ``llm_summary_error == "empty_response"``,
          결정적 trim 결과 유지
    어느 쪽이든 ``llm_summary_attempted`` 는 True, ``out`` 은 비어있지 않음.
    """
    monkeypatch.setenv("LLM_SUMMARY_LAYER_ENABLED", "1")
    monkeypatch.setenv("LLM_SUMMARY_TRIGGER", "chars")

    summarizer = _summarizer()
    text = ("foo " * 4000).strip()
    out, info = asyncio.run(
        trim_text_with_llm_summary(text, max_tokens=200, summarizer=summarizer)
    )

    assert info["llm_summary_attempted"] is True
    assert info["fallback"] in {"llm_summary", "chars", "sentence"}
    assert info["llm_summary_error"] in {"", "empty_response"}
    assert info["llm_summary_retry_count"] in (0, 1)
    assert out


def test_real_trim_text_with_llm_summary_invalid_url_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """잘못된 base_url → graceful fallback (결정적 trim 결과 유지).

    Step 6 의 ``_FailingSummarizer`` mock 을 실 호출로 대체. 잘못된 호스트는
    실제 ``httpx.ConnectError`` / ``ReadTimeout`` 등을 던지므로 Mock 으로 박은
    ``RuntimeError("LM Studio not reachable")`` 보다 더 현실적인 검증.
    """
    monkeypatch.setenv("LLM_SUMMARY_LAYER_ENABLED", "1")
    monkeypatch.setenv("LLM_SUMMARY_TRIGGER", "always")

    summarizer = _summarizer(
        base_url="http://invalid-host-for-step8.localdomain:9999/v1",
        timeout=1.0,
    )
    text = ("한국어 문장. " * 400).strip()
    out, info = asyncio.run(
        trim_text_with_llm_summary(text, max_tokens=200, summarizer=summarizer)
    )

    assert info["llm_summary_attempted"] is True
    assert info["llm_summary_used"] is False
    assert info["llm_summary_error"]  # 비어있지 않은 예외 클래스명
    assert info["trimmed"] is True
    assert out  # 결정적 trim 결과는 항상 보존
