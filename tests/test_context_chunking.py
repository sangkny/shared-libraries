"""agents.context_chunking 단위 테스트 (네트워크 없음)."""
from __future__ import annotations

import json

import asyncio

import pytest

from agents.context_chunking import (
    DEFAULT_UNKNOWN_MODEL_CONTEXT,
    LLM_MODEL_CONTEXT_LIMITS,
    add_overlap_to_chunks,
    analyze_prompt_for_model,
    chunk_prompt_for_model,
    chunking_max_parallel,
    chunking_metrics_snapshot,
    chunking_overlap_ratio,
    chunking_quality_score,
    compact_context_for_budget,
    compress_conversation_history,
    context_chunked,
    estimate_messages_tokens,
    estimate_orchestrator_input_tokens,
    estimate_text_tokens,
    estimate_tokens_multilingual,
    format_chunk_with_preamble,
    merge_chunk_outputs,
    merge_chunks,
    merge_chunks_recursive_pairs,
    merge_chunks_sequential,
    orchestrator_context_compact_enabled,
    parallel_map_chunks,
    prepare_orchestrator_context,
    prioritize_turn_strings,
    resolve_model_context_window,
    run_chunked_prompt,
    semantic_chunking,
    semantic_chunking_with_overlap,
    split_sentences,
    summarize_conversation_history,
    trim_text_to_token_budget,
)
from llm.base import Message


def test_estimate_messages_basic() -> None:
    msgs = [
        Message(role="system", content="x" * 100),
        Message(role="user", content="안녕" * 20),
    ]
    n = estimate_messages_tokens(msgs)
    assert n > 20


def test_estimate_orchestrator_input() -> None:
    tok = estimate_orchestrator_input_tokens("task", {"a": "b"})
    assert tok >= 2


@pytest.mark.asyncio
async def test_parallel_map_chunks() -> None:

    async def up(s: str) -> str:
        return s.upper()

    out = await parallel_map_chunks(["a", "b"], up, max_concurrency=2)
    assert out == ["A", "B"]


def test_merge_schemes() -> None:
    s = merge_chunks_sequential(["a", "b"])
    assert "a" in s and "b" in s
    rp = merge_chunks_recursive_pairs(["1", "2", "3", "4"])
    assert "4" in rp


def test_semantic_chunking_oversized_turn() -> None:
    sentence = ("짧은 문장입니다. ") * 30
    long_turn = sentence * 15
    est = lambda t: len(t) // 2
    chunks = semantic_chunking([long_turn], max_tokens_per_chunk=80, estimator=est)
    assert len(chunks) >= 2
    for c in chunks:
        assert est(c) <= 80


def test_split_sentences() -> None:
    s = split_sentences("첫 줄.\n두 번째.")
    assert len(s) >= 1


@pytest.mark.asyncio
async def test_context_chunked_noop_when_under_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "agents.context_chunking.estimate_llm_chat_payload_tokens",
        lambda *, prompt, system=None: 10,
    )

    @context_chunked(max_input_tokens=10000)
    async def f(*, prompt: str, system: str | None = None) -> str:
        return prompt

    assert await f(prompt="tiny") == "tiny"


@pytest.mark.asyncio
async def test_context_chunked_merges_when_over_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int] = []

    def fake_estimate(*, prompt: str, system: str | None = None) -> int:
        seen.append(len(prompt))
        return max(10, len(prompt))

    monkeypatch.setattr(
        "agents.context_chunking.estimate_llm_chat_payload_tokens",
        fake_estimate,
    )

    @context_chunked(max_input_tokens=200, reserve_tokens_for_system=0)
    async def f(*, prompt: str, system: str | None = None) -> str:
        return f"ok:{len(prompt)}"

    long_p = ("문장입니다. ") * 80
    out = await f(prompt=long_p)
    assert out.startswith("ok:")
    assert int(out.split(":")[1]) < len(long_p)


def test_overlap_increases_follower_size() -> None:
    ch = ["ABCDEFGHIJ" * 5, "NEXT" * 3]
    overl = add_overlap_to_chunks(ch, overlap_ratio=0.2, overlap_chars_min=40)
    assert len(overl) == 2
    assert overl[0] == ch[0]
    assert "NEXT" in overl[1] and "ABCDEF" in overl[1]


def test_semantic_chunking_with_overlap_len() -> None:
    long_t = ("단문장. ") * 120
    chunks = semantic_chunking_with_overlap(
        [long_t],
        max_tokens_per_chunk=48,
        overlap_ratio=0.15,
    )
    assert len(chunks) >= 1


def test_prioritize_turn_strings() -> None:
    t = prioritize_turn_strings(["일반", "검사 결과 이상"], keywords=("검사",))
    assert t[0] == "검사 결과 이상"


def test_chunking_quality_score_basic() -> None:
    qs = chunking_quality_score(
        "원본에 진단 포함",
        "원본에 진단 포함 더붙임",
        priority_keywords=("진단",),
    )
    assert qs["priority_keyword_overlap_count"] >= 1


def test_compact_context() -> None:
    giant = {"a": "x" * 20000}
    comp = compact_context_for_budget(giant, max_payload_tokens=500)
    assert len(json.dumps(comp, ensure_ascii=False)) < len(json.dumps(giant, ensure_ascii=False))


def test_prepare_orchestrator_context_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCH_COMPACT_CONTEXT", "1")
    monkeypatch.setenv("ORCH_CONTEXT_MAX_TOKENS", "800")
    raw = {"h": "y" * 50000}
    out = prepare_orchestrator_context("task", raw)
    assert estimate_orchestrator_input_tokens("task", out) < estimate_orchestrator_input_tokens(
        "task",
        raw,
    )


def test_chunking_env_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHUNKING_MAX_PARALLEL", "8")
    assert chunking_max_parallel() == 8
    monkeypatch.setenv("CHUNKING_OVERLAP_RATIO", "0.22")
    assert abs(chunking_overlap_ratio() - 0.22) < 1e-9


def test_summarize_conversation_history_truncates(monkeypatch: pytest.MonkeyPatch) -> None:
    from collections.abc import Mapping

    monkeypatch.setenv("CHUNKING_HISTORY_MAX_TOKENS", "200")
    monkeypatch.setenv("CHUNKING_CONVERSATION_RECENT_TURNS", "2")
    msgs = [{"role": "user", "content": "x" * 800} for _ in range(10)]
    out = summarize_conversation_history(msgs, max_history_tokens=200, recent_keep=2)
    assert len(out) < len(msgs)
    first = out[0]
    blob = first.get("content", "") if isinstance(first, Mapping) else str(first)
    assert "prior_turns_truncated" in blob


def test_prepare_orchestrator_prunes_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCH_COMPACT_CONTEXT", "1")
    monkeypatch.setenv("ORCH_CONTEXT_MAX_TOKENS", "999999")
    monkeypatch.setenv("CHUNKING_HISTORY_MAX_TOKENS", "80")
    monkeypatch.setenv("CHUNKING_CONVERSATION_RECENT_TURNS", "2")
    msgs = [{"role": "user", "content": "longcontent" * 200} for _ in range(9)]
    raw = {"messages": msgs}
    out = prepare_orchestrator_context("task", raw)
    assert isinstance(out["messages"], list)
    assert len(out["messages"]) < len(msgs)


@pytest.mark.asyncio
async def test_parallel_map_chunks_defaults_read_env(monkeypatch: pytest.MonkeyPatch) -> None:
    sizes: list[int] = []

    RealSem = asyncio.Semaphore

    def factory(n: int) -> asyncio.Semaphore:
        sizes.append(n)
        return RealSem(n)

    monkeypatch.setattr("agents.context_chunking.asyncio.Semaphore", factory)
    monkeypatch.setenv("CHUNKING_MAX_PARALLEL", "4")

    async def up(s: str) -> str:
        return s.upper()

    out = await parallel_map_chunks(["a", "b"], up, max_concurrency=None)
    assert out == ["A", "B"]
    assert sizes and sizes[0] == 4


# ── Paperclip OpenCode 패턴 회귀 ────────────────────────────────
# 참고: paperclip/packages/adapters/opencode-local/src/server/context-chunking-regression.test.ts
# 성공 케이스:
#   1) 짧은 프롬프트 → chunks_needed == 1
#   2) 긴 프롬프트  → chunks_needed >= 2
#   3) 모든 청크 출력이 [Chunk i/N] 결합 문자열로 병합
#   4) 모델 컨텍스트 해상도(exact·lowercase·substring)
#   5) run_chunked_prompt 가 단일/다중 청크 모두 처리
#   6) 환경변수 override 가 모델 한도를 반영


def test_resolve_model_context_window_exact_and_fallback() -> None:
    assert resolve_model_context_window("google/gemma-4-e4b") == LLM_MODEL_CONTEXT_LIMITS[
        "google/gemma-4-e4b"
    ]
    assert resolve_model_context_window("GEMMA-4-E4B") == LLM_MODEL_CONTEXT_LIMITS["gemma-4-e4b"]
    assert (
        resolve_model_context_window("custom/google/gemma-4-e4b@instruct")
        == LLM_MODEL_CONTEXT_LIMITS["gemma-4-e4b"]
    )
    assert resolve_model_context_window(None) == DEFAULT_UNKNOWN_MODEL_CONTEXT
    assert resolve_model_context_window("nonexistent-model-xyz") == DEFAULT_UNKNOWN_MODEL_CONTEXT


def test_resolve_model_context_window_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "LLM_MODEL_CONTEXT_OVERRIDES", '{"my-local/llm-x": 16384, "gemma-4-e4b": 65536}'
    )
    assert resolve_model_context_window("my-local/llm-x") == 16384
    assert resolve_model_context_window("gemma-4-e4b") == 65536


def test_analyze_prompt_short_does_not_trigger_chunking() -> None:
    short_prompt = "환자 진단 요약 요청. 짧은 입력입니다."
    out = analyze_prompt_for_model(short_prompt, model="gemma-4-e4b")
    assert out.chunks_needed == 1
    assert out.fits_context is True
    assert out.model_context_window == LLM_MODEL_CONTEXT_LIMITS["gemma-4-e4b"]
    assert out.effective_submission_budget > 0
    assert "한 번에" in out.recommendation


def test_analyze_prompt_long_triggers_chunking() -> None:
    long_prompt = ("한국어 의무기록 본문이 매우 길게 이어집니다. " * 200 + "\n\n") * 60
    out = analyze_prompt_for_model(long_prompt, model="gemma-4-e4b")
    assert out.fits_context is False
    assert out.chunks_needed >= 2
    assert "청크" in out.recommendation


def test_chunk_prompt_short_one_chunk() -> None:
    chunks = chunk_prompt_for_model("짧은 문장입니다.", model="gemma-4-e4b")
    assert len(chunks) == 1
    assert chunks[0].index == 0
    assert chunks[0].total_chunks == 1


def test_chunk_prompt_long_multiple_chunks_preserve_content() -> None:
    paragraphs = []
    for i in range(120):
        paragraphs.append(f"## 섹션 {i}\n" + ("긴 한국어 문단입니다. " * 80))
    long_prompt = "\n\n".join(paragraphs)
    chunks = chunk_prompt_for_model(long_prompt, model="gemma-4-e4b")
    assert len(chunks) >= 2
    indexes = [c.index for c in chunks]
    assert indexes == list(range(len(chunks)))
    assert all(c.total_chunks == len(chunks) for c in chunks)
    joined = "\n\n".join(c.content for c in chunks)
    assert "섹션 0" in joined
    assert f"섹션 {len(paragraphs) - 1}" in joined


def test_format_chunk_with_preamble_single_chunk_passthrough() -> None:
    assert format_chunk_with_preamble("hello", index=0, total_chunks=1) == "hello"


def test_format_chunk_with_preamble_multi_chunk_adds_header() -> None:
    framed = format_chunk_with_preamble(
        "본문", index=1, total_chunks=3, note="OpenCode 어댑터 호출"
    )
    assert "청크 2/3만" in framed
    assert "OpenCode 어댑터 호출" in framed
    assert framed.endswith("본문")


def test_merge_chunk_outputs_format() -> None:
    merged = merge_chunk_outputs(["첫 결과", "둘째 결과", ""], delimiter="\n---\n")
    assert "[Chunk 1/3]\n첫 결과" in merged
    assert "[Chunk 2/3]\n둘째 결과" in merged
    assert "[Chunk 3/3]" not in merged  # empty skipped
    assert "\n---\n" in merged


def test_merge_chunk_outputs_empty() -> None:
    assert merge_chunk_outputs([]) == ""
    assert merge_chunk_outputs([None, " ", ""]) == ""


@pytest.mark.asyncio
async def test_run_chunked_prompt_single_chunk_calls_runner_once() -> None:
    calls: list[str] = []

    async def runner(prompt: str) -> str:
        calls.append(prompt)
        return f"OK({len(prompt)})"

    merged, analysis, chunks = await run_chunked_prompt(
        "짧은 입력", runner, model="gemma-4-e4b"
    )
    assert len(chunks) == 1
    assert analysis.fits_context is True
    assert len(calls) == 1
    assert merged.startswith("OK(")


@pytest.mark.asyncio
async def test_run_chunked_prompt_multi_chunk_merges_outputs() -> None:
    # gemma-4-e4b 컨텍스트 = 32768, 안전 마진 75% → 제출허용 ≈ 24576 tok.
    # 충분히 넘기도록 240 섹션 × 120 단어로 구성.
    paragraphs = [f"## 섹션 {i}\n" + ("긴 한국어 문단입니다. " * 120) for i in range(240)]
    long_prompt = "\n\n".join(paragraphs)

    import re as _re

    seen_indexes: list[int] = []
    _idx_re = _re.compile(r"청크\s+(\d+)\s*/\s*\d+만")

    async def runner(prompt: str) -> str:
        m = _idx_re.search(prompt)
        if m:
            seen_indexes.append(int(m.group(1)))
        return f"청크 응답 본문[len={len(prompt)}]"

    merged, analysis, chunks = await run_chunked_prompt(
        long_prompt, runner, model="gemma-4-e4b", preamble_note="MEDI/Orchestrator 호출"
    )
    assert analysis.fits_context is False
    assert len(chunks) >= 2
    assert "[Chunk 1/" in merged
    assert f"[Chunk {len(chunks)}/{len(chunks)}]" in merged
    assert seen_indexes == list(range(1, len(chunks) + 1))


# ── 선별 흡수: Python 강점 유지 + Paperclip 효율 흡수 회귀 ───────────────
# 의도: Paperclip 의 1:1 포팅이 아니라 (1) 다국어 토큰 추정, (2) 문자열형 멀티턴 압축,
# (3) 통합 merge_chunks 진입점, (4) 데코레이터 반환 타입 보존, (5) 환경변수 기본 OFF.


def test_estimate_tokens_multilingual_korean_english_overhead() -> None:
    # 한글 6자 → 2 토큰, 영어 8자 → 2 토큰, 합 4 + 12% 오버헤드(0) = 4
    assert estimate_tokens_multilingual("가나다라마바" + "abcdefgh") == 4
    # 한글만 9자 → 3 토큰, 12% 오버헤드(0) = 3
    assert estimate_tokens_multilingual("가나다라마바사아자") == 3
    # 영어만 16자 → 4 토큰, 12% 오버헤드(0) = 4
    assert estimate_tokens_multilingual("a" * 16) == 4
    # 빈 문자열 → 0
    assert estimate_tokens_multilingual("") == 0
    # 한글이 영어보다 더 많은 토큰을 소비해야 함 (같은 문자 수에서)
    han = "가" * 60
    eng = "a" * 60
    assert estimate_tokens_multilingual(han) > estimate_tokens_multilingual(eng)


def test_estimate_text_tokens_fallback_uses_multilingual(monkeypatch: pytest.MonkeyPatch) -> None:
    # tiktoken encoder 를 강제로 비활성화하여 fallback 경로 검증
    monkeypatch.setattr("agents.context_chunking._encoding", lambda: None)
    han = "환자는 진단 결과에 대해 추가 검사 일정을 요청했습니다."
    assert estimate_text_tokens(han) == estimate_tokens_multilingual(han)


def test_multiturn_compression_preserves_recent_and_returns_string() -> None:
    msgs = [
        {"role": "user", "content": f"오래된 질문 {i} " + ("배경 설명 " * 80)}
        for i in range(12)
    ]
    msgs.extend(
        [
            {"role": "user", "content": "최근 질문 A"},
            {"role": "assistant", "content": "최근 답변 A"},
            {"role": "user", "content": "최근 질문 B"},
            {"role": "assistant", "content": "최근 답변 B"},
            {"role": "user", "content": "최근 질문 C"},
        ]
    )
    out = compress_conversation_history(msgs, max_tokens=300, preserve_recent=3)
    assert isinstance(out, str)
    # 최근 3 턴은 원문 보존
    assert "최근 질문 B" in out
    assert "최근 답변 B" in out
    assert "최근 질문 C" in out
    # 오래된 턴은 접혀서 사라지거나 스텁으로 압축됨
    assert "오래된 질문 0" not in out
    # role 라벨이 포함됨
    assert "[user]" in out


def test_multiturn_compression_empty_input() -> None:
    assert compress_conversation_history([]) == ""


def test_merge_chunks_unified_dispatch() -> None:
    chs = ["가 청크 본문", "나 청크 본문", "다 청크 본문"]
    seq = merge_chunks(chs, strategy="sequential")
    rec = merge_chunks(chs, strategy="recursive_pair")
    idx = merge_chunks(chs, strategy="indexed")
    # sequential / recursive_pair 는 모든 본문을 포함해야 함
    for label, text in (("seq", seq), ("rec", rec)):
        for token in ("가 청크 본문", "나 청크 본문", "다 청크 본문"):
            assert token in text, f"{label} missing {token}"
    # indexed (Paperclip) 는 [Chunk i/N] 마커가 있어야 함
    assert "[Chunk 1/3]" in idx and "[Chunk 3/3]" in idx
    # 별칭(`pairwise`, `paperclip`)도 받아야 함
    assert merge_chunks(chs, strategy="pairwise") == rec
    assert merge_chunks(chs, strategy="paperclip") == idx


def test_merge_chunks_recursive_vs_sequential_content_equivalence() -> None:
    # 결합 트리 깊이만 다르고 결과 본문 집합은 동일해야 함
    chs = [f"청크-{i} 본문 토큰" for i in range(8)]
    seq = merge_chunks(chs, strategy="sequential")
    rec = merge_chunks(chs, strategy="recursive_pair")
    for i in range(8):
        assert f"청크-{i} 본문 토큰" in seq
        assert f"청크-{i} 본문 토큰" in rec


def test_merge_chunks_unknown_strategy_raises() -> None:
    with pytest.raises(ValueError):
        merge_chunks(["a", "b"], strategy="totally-unknown-xyz")


@pytest.mark.asyncio
async def test_context_chunked_decorator_preserves_return_type(monkeypatch: pytest.MonkeyPatch) -> None:
    # 큰 추정값을 강제하여 청킹 경로를 활성화한 뒤에도 string 반환 타입이 유지되는지
    def fake_estimate(*, prompt: str, system: str | None = None) -> int:
        return max(10, len(prompt))

    monkeypatch.setattr(
        "agents.context_chunking.estimate_llm_chat_payload_tokens",
        fake_estimate,
    )

    @context_chunked(max_input_tokens=200, reserve_tokens_for_system=0)
    async def echo(*, prompt: str, system: str | None = None) -> str:
        return prompt

    short = await echo(prompt="짧은 입력")
    assert isinstance(short, str)
    assert short == "짧은 입력"

    long_p = ("문장 입력입니다. " * 80)
    long_out = await echo(prompt=long_p)
    assert isinstance(long_out, str)
    assert len(long_out) > 0


def test_orchestrator_compact_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    # 환경변수 미설정 시 결정적 OFF — 점진 도입 안전 가드
    monkeypatch.delenv("ORCH_COMPACT_CONTEXT", raising=False)
    assert orchestrator_context_compact_enabled() is False
    monkeypatch.setenv("ORCH_COMPACT_CONTEXT", "0")
    assert orchestrator_context_compact_enabled() is False
    monkeypatch.setenv("ORCH_COMPACT_CONTEXT", "1")
    assert orchestrator_context_compact_enabled() is True


def test_prepare_orchestrator_context_default_off_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    # ORCH_COMPACT_CONTEXT 가 OFF 면 prepare_orchestrator_context 가 큰 입력도 변형 없이 통과
    monkeypatch.delenv("ORCH_COMPACT_CONTEXT", raising=False)
    raw = {"messages": [{"role": "user", "content": "x" * 5000} for _ in range(8)]}
    out = prepare_orchestrator_context("task", raw)
    assert len(out["messages"]) == len(raw["messages"])
    assert out["messages"][0]["content"] == raw["messages"][0]["content"]


# ── 전문가 후속 제안 흡수 — overlap_ratio 노출 + 메트릭 스냅샷 ─────────────


def test_chunk_prompt_overlap_increases_content_size() -> None:
    paragraphs = [f"## 섹션 {i}\n" + ("긴 한국어 문단입니다. " * 120) for i in range(240)]
    long_prompt = "\n\n".join(paragraphs)
    base = chunk_prompt_for_model(long_prompt, model="gemma-4-e4b")
    with_overlap = chunk_prompt_for_model(long_prompt, model="gemma-4-e4b", overlap_ratio=0.2)
    assert len(base) >= 2 and len(with_overlap) >= 2
    # 오버랩이 적용된 두 번째 청크는 "이전 청크 연속" 프리픽스가 붙어 본문이 더 길다
    assert "이전 청크 연속" in with_overlap[1].content
    assert with_overlap[1].token_count >= base[1].token_count
    # 첫 청크는 그대로
    assert with_overlap[0].content == base[0].content


@pytest.mark.asyncio
async def test_run_chunked_prompt_with_overlap_routes_through() -> None:
    paragraphs = [f"## 섹션 {i}\n" + ("긴 한국어 문단입니다. " * 120) for i in range(240)]
    long_prompt = "\n\n".join(paragraphs)
    captured: list[str] = []

    async def runner(p: str) -> str:
        captured.append(p)
        return "ok"

    merged, analysis, chunks = await run_chunked_prompt(
        long_prompt,
        runner,
        model="gemma-4-e4b",
        overlap_ratio=0.15,
    )
    assert len(chunks) >= 2
    # 두 번째 청크 이상에 오버랩 마커 존재
    assert any("이전 청크 연속" in c.content for c in chunks[1:])
    assert "[Chunk 1/" in merged and analysis.fits_context is False


def test_chunking_metrics_snapshot_shape() -> None:
    paragraphs = [f"## 섹션 {i}\n" + ("긴 한국어 문단입니다. " * 120) for i in range(240)]
    long_prompt = "\n\n".join(paragraphs)
    analysis = analyze_prompt_for_model(long_prompt, model="gemma-4-e4b")
    chunks = chunk_prompt_for_model(long_prompt, model="gemma-4-e4b")
    snap = chunking_metrics_snapshot(analysis, chunks, extra={"trace_id": "abc"})
    expected_keys = {
        "chunking_model",
        "chunking_model_context_window",
        "chunking_submission_budget",
        "chunking_chunk_token_budget",
        "chunking_body_tokens_estimated",
        "chunking_total_tokens_estimated",
        "chunking_chunks_needed",
        "chunking_chunks_produced",
        "chunking_fits_context",
        "chunking_overlap_inflation_ratio",
        "chunking_recommendation",
        "trace_id",
    }
    assert expected_keys.issubset(snap.keys())
    assert snap["chunking_chunks_produced"] == len(chunks)
    assert snap["chunking_model"] == "gemma-4-e4b"
    assert snap["chunking_fits_context"] is False
    # 오버랩 비적용이면 인플레이션 비율은 1.0 근처(또는 그 미만)
    assert snap["chunking_overlap_inflation_ratio"] <= 1.05
    assert snap["trace_id"] == "abc"


def test_chunking_metrics_snapshot_inflation_with_overlap() -> None:
    paragraphs = [f"## 섹션 {i}\n" + ("긴 한국어 문단입니다. " * 120) for i in range(240)]
    long_prompt = "\n\n".join(paragraphs)
    analysis = analyze_prompt_for_model(long_prompt, model="gemma-4-e4b")
    chunks = chunk_prompt_for_model(long_prompt, model="gemma-4-e4b", overlap_ratio=0.2)
    snap = chunking_metrics_snapshot(analysis, chunks)
    # 오버랩 → 청크 토큰 합이 원본 본문 추정보다 커져야 함
    assert snap["chunking_overlap_inflation_ratio"] > 1.0


# ── trim_text_to_token_budget (Reviewer 입력 압축용 단일-텍스트 헬퍼) ─────


def test_trim_text_no_op_when_within_budget() -> None:
    text = "안녕하세요. 짧은 문장입니다. 이건 그대로 통과해야 합니다."
    out, info = trim_text_to_token_budget(text, max_tokens=10_000)
    assert out == text
    assert info["trimmed"] is False
    assert info["dropped_tokens"] == 0
    assert info["pre_tokens"] == info["post_tokens"]


def test_trim_text_sentence_aware_drops_middle() -> None:
    sentences = [f"이 문장은 번호 {i} 입니다. 한국어 문장 토큰 추정 회귀용 입력." for i in range(200)]
    text = " ".join(sentences)
    pre = estimate_text_tokens(text)
    out, info = trim_text_to_token_budget(text, max_tokens=300)
    post = estimate_text_tokens(out)
    assert info["trimmed"] is True
    assert post <= info["budget"] * 1.1  # 트림 후 budget 근처
    assert post < pre
    assert "중간 약" in out and "토큰 생략" in out
    # 앞·뒤 보존 — 첫 문장과 마지막 문장이 보존되어야 함
    assert sentences[0] in out
    assert sentences[-1] in out
    assert info["fallback"] in {"sentence", "chars"}


def test_trim_text_char_fallback_for_singleline_huge_input() -> None:
    # 문장 경계가 거의 없는 한 줄짜리 대형 입력 — char 폴백 진입.
    text = "한국어임상메모반복" * 5000  # 약 45,000자 한 덩어리
    pre = estimate_text_tokens(text)
    out, info = trim_text_to_token_budget(text, max_tokens=400)
    post = estimate_text_tokens(out)
    assert info["trimmed"] is True
    assert info["fallback"] == "chars"
    # 가혹한 단일-덩어리 입력에서는 final safety cut 까지 갈 수 있으므로 후하게 본다.
    assert post <= 800
    assert post < pre
    # 최소한 head 는 보존되어야 한다(생략 표시는 항상 포함).
    assert out.startswith("한국어임상메모반복")
    assert "토큰 생략" in out


def test_trim_text_returns_info_with_required_keys() -> None:
    text = "문장입니다." * 500
    _, info = trim_text_to_token_budget(text, max_tokens=100)
    required = {
        "pre_tokens",
        "post_tokens",
        "dropped_tokens",
        "trimmed",
        "head_tokens",
        "tail_tokens",
        "budget",
        "fallback",
    }
    assert required.issubset(info.keys())
    assert info["budget"] == 100
    assert info["trimmed"] is True
    assert info["dropped_tokens"] > 0


def test_trim_text_idempotent_within_budget_after_first_pass() -> None:
    text = ("긴 한국어 문장. " * 400).strip()
    out1, info1 = trim_text_to_token_budget(text, max_tokens=300)
    out2, info2 = trim_text_to_token_budget(out1, max_tokens=300)
    # 한 번 trim 된 결과는 다시 호출해도 더 줄지 않아야(또는 거의 변동 없음) 한다.
    assert info1["trimmed"] is True
    assert info2["trimmed"] is False or info2["post_tokens"] <= info1["post_tokens"]