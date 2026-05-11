# shared-libraries/agents/context_chunking.py
"""
컨텍스트 예산: 토큰 추정 · 시맨틱 청킹 · 병렬 전처리 · @context_chunked

병합 전략(코드베이스 적합성):
- **sequential_concat** (권장): 순차 문자열 결합 — Orchestrator Lore·재현성에 유리.
- **recursive_pair**: 청크가 많을 때 쌍 접합 트리로 결합 문자열 깊이 완화.
- **cross-attention**: Python 문자열 병합 단계에서는 구현 대상 아님(모델 내부 메커니즘).

tiktoken 미설치 시 문자/4 근사.

**Orchestrator**: ``@context_chunked``는 ``prompt`` kwargs 패턴이라 ``execute()``에 직접 부착하기 어렵다.
환경변수 ``ORCH_COMPACT_CONTEXT`` 로 ``compact_context_for_budget`` 적용 시 context JSON 길이를 실제로 줄인다.
대화 기록은 동일 조건에서 ``CHUNKING_HISTORY_MAX_TOKENS`` / ``CHUNKING_CONVERSATION_RECENT_TURNS`` 로
``messages`` 등 키에 한해 ``summarize_conversation_history`` 와 동일한 규칙 접기를 선행한다.

**오버랩**: 요약·청크 경계 정보 손실 완화. ``CHUNKING_OVERLAP_RATIO`` 로 ``@context_chunked`` 기본값을 덮어쓸 수 있다.
"""
from __future__ import annotations

import asyncio
import copy
import functools
import json
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ParamSpec, TypeVar

from llm.base import Message

logger = logging.getLogger("agents.context_chunking")

P = ParamSpec("P")
R = TypeVar("R")

DEFAULT_PRIORITY_KEYWORDS: tuple[str, ...] = (
    "증상",
    "검사",
    "진단",
    "처방",
    "icd",
    "소견",
    "HbA1c",
    "망막",
)


def _get_encoder() -> Any | None:
    try:
        import tiktoken  # type: ignore[import-untyped]

        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None


_ENC: Any | None | bool = None


def _encoding() -> Any | None:
    global _ENC
    if _ENC is False:
        return None
    if _ENC is None:
        got = _get_encoder()
        if got is None:
            _ENC = False
            logger.debug("tiktoken 미사용 — 문자/4 근사")
            return None
        _ENC = got
        return got
    return _ENC


_HANGUL_SYLLABLE_RE = re.compile(r"[\uAC00-\uD7AF]")


def estimate_tokens_multilingual(text: str) -> int:
    """
    한글/영어를 별도 비율로 추정하는 fallback 토크나이저.

    - 한글(precomposed Hangul ``U+AC00-U+D7AF``): **3자/토큰**
    - 그 외(영어·숫자·공백 포함): **4자/토큰**
    - 구조 오버헤드(공백·구분자·BOS/EOS 등): 위 두 토큰의 합에 **+12%**

    Paperclip ``token-estimator.ts`` 의 다국어 보정 아이디어를 흡수한 사양이며
    ``tiktoken`` 미사용 시 ``estimate_text_tokens`` 가 이 함수를 호출한다.
    """
    if not text:
        return 0
    korean_chars = len(_HANGUL_SYLLABLE_RE.findall(text))
    other_chars = max(0, len(text) - korean_chars)
    korean_tokens = (korean_chars + 2) // 3 if korean_chars > 0 else 0
    other_tokens = (other_chars + 3) // 4 if other_chars > 0 else 0
    base = korean_tokens + other_tokens
    if base == 0:
        return 1
    overhead = int(base * 0.12)
    return base + overhead


def estimate_text_tokens(text: str) -> int:
    enc = _encoding()
    if enc is None:
        return estimate_tokens_multilingual(text)
    try:
        return len(enc.encode(text))
    except Exception:
        return estimate_tokens_multilingual(text)


def _msg_content_part(m: Message | Mapping[str, Any]) -> str:
    if isinstance(m, Message):
        return m.content or ""
    return str((m.get("content") if isinstance(m, Mapping) else "") or "")


def estimate_messages_tokens(
    messages: Sequence[Message | Mapping[str, Any]],
    *,
    per_message_overhead: int = 4,
) -> int:
    """messages 리스트에 대한 입력 토큰 추정."""
    total = 0
    for m in messages:
        role = ""
        if isinstance(m, Mapping):
            role = str(m.get("role") or "")
        elif isinstance(m, Message):
            role = m.role
        txt = _msg_content_part(m)
        total += per_message_overhead + len(role) // 4 + estimate_text_tokens(txt)
    return total


def estimate_llm_chat_payload_tokens(*, prompt: str, system: str | None = None) -> int:
    msgs: list[Message] = []
    if system:
        msgs.append(Message(role="system", content=system))
    msgs.append(Message(role="user", content=prompt))
    return estimate_messages_tokens(msgs)


def estimate_llm_request_tokens(request_messages: Sequence[Message | Mapping[str, Any]]) -> int:
    """LLMRequest.messages 용량 추정(Orchestrator·Provider 공통 헬프)."""
    return estimate_messages_tokens(request_messages)


def estimate_orchestrator_input_tokens(task: str, context: Mapping[str, Any] | None) -> int:
    """Orchestrator.execute(task, context) 입력 문자열 크기 간접 추정."""
    ctx = context or {}
    try:
        tail = json.dumps(ctx, ensure_ascii=False, default=str)[:24000]
    except Exception:
        tail = str(ctx)[:24000]
    blob = f"{task}\n{tail}"
    return estimate_text_tokens(blob)


# ── 문장 분리 / 시맨틱 청킹 ────────────────────────────────────

_SENT_SPLIT = re.compile(r"(?<=[.!?。？！])\s+|\r?\n{2,}")


def split_sentences(text: str) -> list[str]:
    raw = text.strip()
    if not raw:
        return []
    bits: list[str] = []
    buf = ""
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            if buf:
                bits.append(buf)
                buf = ""
            continue
        sub = _SENT_SPLIT.split(line)
        for seg in sub:
            s = seg.strip()
            if not s:
                continue
            tentative = (buf + " " + s).strip() if buf else s
            if len(tentative) > 8 and tentative[-1] in ".!?。？！":
                bits.append(tentative)
                buf = ""
            else:
                buf = tentative
    if buf:
        bits.append(buf)
    return bits if bits else [raw]


# ── 단일 텍스트 토큰 트림 (Reviewer / Fixer 등 단일-호출 에이전트용) ─────
#
# 청킹/멀티턴 압축과 별개로, "한 번의 LLM 호출에 들어가는 단일 텍스트 필드"
# (예: Orchestrator 가 Reviewer 에게 전달하는 ``task``) 가 너무 클 때 사용한다.
# 거동은 그대로 두고 입력만 토큰 예산 안으로 절단한다 — 앞·뒤를 보존하고 가운데를
# 의도적으로 표시 후 생략. 문장 경계가 잡히면 그걸 우선한다.


def trim_text_to_token_budget(
    text: str,
    *,
    max_tokens: int,
    head_ratio: float = 0.7,
    tail_ratio: float = 0.25,
    elision_template: str = "\n\n…[중간 약 {dropped} 토큰 생략 (Reviewer 입력 예산 보호)]…\n\n",
) -> tuple[str, dict[str, Any]]:
    """
    ``text`` 의 토큰 추정치가 ``max_tokens`` 를 넘으면 앞 ``head_ratio`` + 뒤 ``tail_ratio``
    만 보존하고 가운데를 ``elision_template`` 한 줄로 압축한다.

    문장 경계가 잡히면 그걸 우선해서 자르고, 문장이 너무 큰 경우(또는 단일 문장 입력)
    에는 문자 단위로 폴백한다. 합산이 여전히 ``max_tokens`` 를 넘는 경우(주로 한 문장이
    budget 자체보다 큰 케이스)에는 최종 문자열을 추가 trim 한다.

    Returns:
        ``(trimmed_text, info)`` 튜플. ``info`` 는 항상 다음 키를 포함한다:
            ``pre_tokens``, ``post_tokens``, ``dropped_tokens``, ``trimmed`` (bool),
            ``head_tokens``, ``tail_tokens``, ``budget``, ``fallback`` (str).
    """
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    budget = max(1, int(max_tokens))
    pre = estimate_text_tokens(text)
    info: dict[str, Any] = {
        "pre_tokens": pre,
        "post_tokens": pre,
        "dropped_tokens": 0,
        "trimmed": False,
        "head_tokens": pre,
        "tail_tokens": 0,
        "budget": budget,
        "fallback": "none",
    }
    if pre <= budget:
        return text, info

    head_ratio = min(0.95, max(0.0, float(head_ratio)))
    tail_ratio = min(0.95 - head_ratio, max(0.0, float(tail_ratio)))
    head_budget = max(1, int(budget * head_ratio))
    tail_budget = max(0, int(budget * tail_ratio))

    sentences = split_sentences(text)

    def _pack_from_head(sents: list[str], cap: int) -> tuple[str, int, int]:
        out: list[str] = []
        used = 0
        used_idx = 0
        for i, s in enumerate(sents):
            tok = estimate_text_tokens((" ".join(out + [s])).strip())
            if out and tok > cap:
                break
            out.append(s)
            used = tok
            used_idx = i + 1
        return (" ".join(out).strip(), used, used_idx)

    def _pack_from_tail(sents: list[str], cap: int) -> tuple[str, int, int]:
        if cap <= 0 or not sents:
            return "", 0, 0
        out: list[str] = []
        used = 0
        used_count = 0
        for s in reversed(sents):
            tok = estimate_text_tokens((" ".join([s] + out)).strip())
            if out and tok > cap:
                break
            out.insert(0, s)
            used = tok
            used_count += 1
        return (" ".join(out).strip(), used, used_count)

    fallback = "sentence"
    if len(sentences) >= 2:
        head_text, head_tok, head_used = _pack_from_head(sentences, head_budget)
        remaining = max(0, sentences and (len(sentences) - head_used) or 0)
        tail_text, tail_tok, tail_used = _pack_from_tail(
            sentences[head_used:], tail_budget
        )
        if head_used + tail_used >= len(sentences):
            # head + tail 합이 원본을 덮음 — 절단 불필요.
            return text, info
        if not head_text and not tail_text:
            fallback = "chars"
        else:
            dropped_tokens = max(0, pre - head_tok - tail_tok)
            elision = elision_template.format(dropped=dropped_tokens)
            combined = head_text + elision + tail_text
            post = estimate_text_tokens(combined)
            if post <= budget:
                info.update(
                    pre_tokens=pre,
                    post_tokens=post,
                    dropped_tokens=max(0, pre - post),
                    trimmed=True,
                    head_tokens=head_tok,
                    tail_tokens=tail_tok,
                    budget=budget,
                    fallback=fallback,
                )
                return combined, info
            # 합산이 여전히 budget 초과면 문자 폴백.
            fallback = "chars"
    else:
        fallback = "chars"

    # ── 문자 단위 폴백 ─────────────────────────────────────────
    # head/tail 비율을 문자수에 그대로 투영.
    total_chars = len(text)
    head_chars = max(1, int(total_chars * head_ratio))
    tail_chars = max(0, int(total_chars * tail_ratio))
    if head_chars + tail_chars >= total_chars:
        # 비율이 100% 이상 → trim 무의미.
        return text, info
    head_part = text[:head_chars]
    tail_part = text[-tail_chars:] if tail_chars else ""
    dropped_tokens = max(
        0, pre - estimate_text_tokens(head_part) - estimate_text_tokens(tail_part)
    )
    elision = elision_template.format(dropped=dropped_tokens)
    combined = head_part + elision + tail_part
    # 그래도 초과면 head 비율을 절반으로 줄이며 재시도(최대 8회).
    safety = 8
    while estimate_text_tokens(combined) > budget and safety > 0:
        safety -= 1
        head_chars = max(1, head_chars // 2)
        tail_chars = max(0, tail_chars // 2)
        head_part = text[:head_chars]
        tail_part = text[-tail_chars:] if tail_chars else ""
        dropped_tokens = max(
            0,
            pre - estimate_text_tokens(head_part) - estimate_text_tokens(tail_part),
        )
        combined = (
            head_part + elision_template.format(dropped=dropped_tokens) + tail_part
        )

    # 최종 안전 cut — 그래도 초과하면 elision 만 남기고 head 를 char 비례로 hard cut.
    # 이 케이스는 한 덩어리 비-한국어/공백 입력에서 토큰 추정 오차로 발생할 수 있다.
    if estimate_text_tokens(combined) > budget:
        # 추정상 한 토큰 당 약 3~4자 → budget * 3 자 정도가 안전.
        hard_chars = max(8, int(budget * 2.5))
        head_part = text[:hard_chars]
        tail_part = ""
        dropped_tokens = max(0, pre - estimate_text_tokens(head_part))
        combined = head_part + elision_template.format(dropped=dropped_tokens)
    post = estimate_text_tokens(combined)
    info.update(
        pre_tokens=pre,
        post_tokens=post,
        dropped_tokens=max(0, pre - post),
        trimmed=True,
        head_tokens=estimate_text_tokens(head_part),
        tail_tokens=estimate_text_tokens(tail_part),
        budget=budget,
        fallback=fallback,
    )
    return combined, info


def _pack_sentences_into_budget(
    sentences: list[str],
    max_tokens: int,
    estimator: Callable[[str], int],
) -> list[str]:
    buckets: list[str] = []
    cur: list[str] = []

    def pack_tok(parts: list[str]) -> int:
        return estimator("\n".join(parts))

    cur_tok = 0

    for s in sentences:
        need = estimator(s + "\n") + (1 if cur else 0)
        if cur and cur_tok + need > max_tokens:
            buckets.append("\n".join(cur))
            cur = [s]
            cur_tok = estimator(s)
        else:
            cur.append(s)
            cur_tok = pack_tok(cur)
    if cur:
        buckets.append("\n".join(cur))
    return [b for b in buckets if b.strip()]


def semantic_chunking(
    conversation_turns: Sequence[str],
    *,
    max_tokens_per_chunk: int,
    estimator: Callable[[str], int] | None = None,
) -> list[str]:
    """대화 턴 단위 → 초과분은 문장 경계에서 추가 분할."""
    est = estimator or estimate_text_tokens
    out: list[str] = []

    mt = max(48, max_tokens_per_chunk)

    for turn in conversation_turns:
        t = (turn or "").strip()
        if not t:
            continue
        if est(t) <= mt:
            out.append(t)
            continue
        sentences = split_sentences(t)
        packs = _pack_sentences_into_budget(sentences, mt, est)
        if not packs:
            cap = mt * 4
            out.append(t[:cap] if len(t) > cap else t)
        else:
            out.extend(packs)

    return out


def add_overlap_to_chunks(
    chunks: Sequence[str],
    *,
    overlap_ratio: float = 0.15,
    overlap_chars_min: int = 120,
    prefix_label: str = "[이전 청크 연속]",
) -> list[str]:
    """
    각 청크 앞에 이전 청크 꼬리를 붙여 경계 정보 손실을 완화.
    overlap_ratio: 이전 청크 문자열 길이 대비 최대 비율(기본 15%, 상한 45%).
    """
    ch = [str(c).strip() for c in chunks if str(c).strip()]
    if len(ch) <= 1:
        return list(ch)

    r = float(min(0.45, max(0.03, overlap_ratio)))
    out: list[str] = [ch[0]]
    for i in range(1, len(ch)):
        prev = ch[i - 1]
        take = max(overlap_chars_min, int(len(prev) * r))
        tail = prev[-take:].strip()
        out.append(f"{prefix_label}\n{tail}\n\n{ch[i]}" if tail else ch[i])
    return out


def semantic_chunking_with_overlap(
    conversation_turns: Sequence[str],
    *,
    max_tokens_per_chunk: int,
    overlap_ratio: float = 0.15,
    overlap_chars_min: int = 120,
    estimator: Callable[[str], int] | None = None,
) -> list[str]:
    """먼저 (1-ratio)예산으로 semantic_chunk 후 오버랩 접합."""
    est = estimator or estimate_text_tokens
    inner = max(48, int(max_tokens_per_chunk * (1 - min(0.4, overlap_ratio))))
    chunks = semantic_chunking(
        conversation_turns,
        max_tokens_per_chunk=inner,
        estimator=est,
    )
    return add_overlap_to_chunks(
        chunks,
        overlap_ratio=overlap_ratio,
        overlap_chars_min=overlap_chars_min,
    )


def prioritize_turn_strings(
    turns: Sequence[str],
    keywords: Sequence[str] = DEFAULT_PRIORITY_KEYWORDS,
    *,
    case_insensitive: bool = True,
) -> list[str]:
    """키워드를 포함하는 턴을 앞으로 두는 안정 정렬."""

    def score(t: str) -> int:
        tt = t.lower() if case_insensitive else t
        return sum(
            1
            for kw in keywords
            if (kw.lower() if case_insensitive else kw) in tt
        )

    indexed = list(enumerate(str(x).strip() for x in turns if str(x).strip()))
    indexed.sort(key=lambda iv: (-score(iv[1]), iv[0]))
    return [t for _, t in indexed]


def chunking_quality_score(
    original_payload: str,
    merged_result: str,
    *,
    priority_keywords: Sequence[str] = DEFAULT_PRIORITY_KEYWORDS,
    estimator: Callable[[str], int] | None = None,
) -> dict[str, Any]:
    """청크·병합 후 품질 간이 지표(학습 coherence 모델 없음)."""
    est = estimator or estimate_text_tokens
    ot = float(est(original_payload))
    mt_ok = float(est(merged_result))
    completeness_ratio = mt_ok / max(1.0, ot)

    lowered_m = merged_result.lower()
    orig_l = original_payload.lower()
    prio_in_orig = sum(1 for kw in priority_keywords if kw.lower() in orig_l)
    preserved = sum(
        1 for kw in priority_keywords
        if kw.lower() in orig_l and kw.lower() in lowered_m
    )

    return {
        "completeness_length_ratio": round(completeness_ratio, 4),
        "estimated_input_tokens_original": int(ot),
        "estimated_input_tokens_merged": int(mt_ok),
        "information_loss_approx": round(max(0.0, 1.0 - min(1.5, completeness_ratio)), 4),
        "priority_keyword_overlap_count": preserved,
        "priority_keyword_recall": round(
            min(1.0, preserved / max(1, prio_in_orig)),
            4,
        ),
        "coherence_score": None,
    }


def _context_blob_tokens(ctx: Mapping[str, Any], est: Callable[[str], int]) -> int:
    try:
        blob = json.dumps(dict(ctx), ensure_ascii=False, default=str)
    except Exception:
        blob = str(ctx)
    return est(blob)


def compact_context_for_budget(
    ctx: Mapping[str, Any] | None,
    *,
    max_payload_tokens: int = 14000,
    min_string_cap: int = 400,
    estimator: Callable[[str], int] | None = None,
) -> dict[str, Any]:
    """context dict 를 결정적으로 압축한 복사본."""
    est = estimator or estimate_text_tokens
    if not ctx:
        return {}

    def shrink(obj: Any, cap: int, depth: int) -> Any:
        if depth > 10:
            return "<max-depth>"
        if obj is None or isinstance(obj, (bool, int, float)):
            return obj
        if isinstance(obj, str):
            if len(obj) <= cap:
                return obj
            return obj[:cap] + f"...[omit {len(obj) - cap} chars]"
        if isinstance(obj, Mapping):
            return {str(k): shrink(v, cap, depth + 1) for k, v in obj.items()}
        if isinstance(obj, list):
            return [shrink(x, cap, depth + 1) for x in obj[:200]]
        if isinstance(obj, tuple):
            return tuple(shrink(x, cap, depth + 1) for x in obj[:200])
        try:
            s = repr(obj)
        except Exception:
            s = str(type(obj).__name__)
        return s[:cap] + ("..." if len(s) > cap else "")

    cap = max(min_string_cap, 800)

    shrunk: Any = {}

    while cap >= min_string_cap:
        shrunk = shrink(copy.deepcopy(dict(ctx)), cap, 0)
        if isinstance(shrunk, Mapping) and _context_blob_tokens(dict(shrunk), est) <= max_payload_tokens:
            return dict(shrunk)
        cap = int(cap * 0.75)

    return dict(shrunk) if isinstance(shrunk, Mapping) else {}


# ── 모델-인지 분석/청킹 (Paperclip OpenCode adapter 패턴 포팅) ────────────
# 참고: paperclip/packages/adapters/opencode-local/src/server/{token-estimator,prompt-chunker,context-manager}.ts
# 핵심 아이디어:
#   1) 모델별 컨텍스트 한도 테이블 + 안전 마진(75%)으로 효과 입력 예산을 계산.
#   2) 시스템 프롬프트·도구 출력·응답 여유분을 SUBMISSION_FIXED_OVERHEAD 로 별도 차감.
#   3) "본문 단독(estimateChunkableOnly)" vs "전체 제출(estimate)" 추정을 분리.
#   4) 분석 결과(PromptAnalysis)와 청크 본문(PromptChunk) 을 두 단계로 노출.
#   5) 청크 실행 후 [Chunk i/N] 구분자로 결합(merge_chunk_outputs).

LLM_MODEL_CONTEXT_LIMITS: dict[str, int] = {
    # 로컬 (LM Studio · 추천)
    "google/gemma-4-e4b": 32_768,
    "gemma-4-e4b": 32_768,
    "google/gemma-4-26b-a4b": 32_768,
    "gemma-4-26b-a4b": 32_768,
    "mistralai/mistral-7b-instruct-v0.3": 32_768,
    "mistralai/mistral-7b-instruct": 32_768,
    "mistral-7b-instruct": 32_768,
    "mistral": 32_768,
    # 클라우드 fallback
    "gpt-4o-mini": 128_000,
    "gpt-4o": 128_000,
    "claude-haiku-4-5-20251001": 200_000,
    "claude-haiku-4-5": 200_000,
    "claude-sonnet-4-6": 200_000,
    "gemini-1.5-pro": 1_000_000,
    "gemini-2.0-flash": 1_000_000,
}

# 안전 마진: 모델 컨텍스트 윈도우의 75%만 실제 입력에 사용.
INPUT_SAFETY_FRACTION_DEFAULT = 0.75
# 제출 시 추가로 차감할 고정 오버헤드(시스템 프롬프트·툴 출력·응답 토큰 등) 추정.
SUBMISSION_FIXED_OVERHEAD_TOKENS = 896
MIN_CHUNK_TOKEN_TARGET = 1_024
MAX_CHUNK_TOKEN_TARGET = 12_288
DEFAULT_UNKNOWN_MODEL_CONTEXT = 24_000


def _parse_model_context_overrides() -> dict[str, int]:
    """``LLM_MODEL_CONTEXT_OVERRIDES`` JSON(``{"name":int}``) 또는 ``k=v,k=v`` 포맷 파싱."""
    raw = os.getenv("LLM_MODEL_CONTEXT_OVERRIDES", "").strip()
    if not raw:
        return {}
    overrides: dict[str, int] = {}
    try:
        obj = json.loads(raw)
    except Exception:
        obj = None
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            try:
                overrides[str(k)] = max(2_048, int(v))
            except (ValueError, TypeError):
                continue
        return overrides
    for part in raw.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            try:
                overrides[k.strip()] = max(2_048, int(v.strip()))
            except ValueError:
                continue
    return overrides


def resolve_model_context_window(model_label: str | None) -> int:
    """exact → lowercase → longest-substring 순으로 모델 라벨에 컨텍스트 한도를 매칭."""
    if not model_label:
        return DEFAULT_UNKNOWN_MODEL_CONTEXT
    raw = model_label.strip()
    if not raw:
        return DEFAULT_UNKNOWN_MODEL_CONTEXT

    overrides = _parse_model_context_overrides()
    if raw in overrides:
        return overrides[raw]
    if raw in LLM_MODEL_CONTEXT_LIMITS:
        return LLM_MODEL_CONTEXT_LIMITS[raw]

    lower = raw.lower()
    for src in (overrides, LLM_MODEL_CONTEXT_LIMITS):
        for k, v in src.items():
            if k.lower() == lower:
                return v

    best_len = 0
    best_value = 0
    for src in (overrides, LLM_MODEL_CONTEXT_LIMITS):
        for k, v in src.items():
            lk = k.lower()
            if lk and lk in lower and len(lk) > best_len:
                best_len = len(lk)
                best_value = v
    return best_value if best_value > 0 else DEFAULT_UNKNOWN_MODEL_CONTEXT


def _max_submission_estimate_allowed(window: int, fraction: float) -> int:
    return max(
        MIN_CHUNK_TOKEN_TARGET + SUBMISSION_FIXED_OVERHEAD_TOKENS,
        int(window * fraction),
    )


def _suggested_chunk_budget(window: int, fraction: float) -> int:
    allowed = _max_submission_estimate_allowed(window, fraction)
    content_cap = int((allowed - SUBMISSION_FIXED_OVERHEAD_TOKENS) * 0.92)
    return max(MIN_CHUNK_TOKEN_TARGET, min(MAX_CHUNK_TOKEN_TARGET, content_cap))


@dataclass(frozen=True)
class PromptAnalysis:
    """analyze_prompt_for_model 결과. Paperclip 의 ``analyzeContext`` 와 1:1 대응."""

    model: str
    model_context_window: int
    effective_submission_budget: int  # 모델창 × safety_fraction
    chunk_token_budget: int  # 개별 청크 본문 추정 상한
    total_tokens: int  # 본문 + 오버헤드 추정
    body_tokens: int
    chunks_needed: int
    fits_context: bool
    recommendation: str


def analyze_prompt_for_model(
    prompt: str,
    *,
    model: str | None = None,
    system: str | None = None,
    safety_fraction: float = INPUT_SAFETY_FRACTION_DEFAULT,
) -> PromptAnalysis:
    """프롬프트가 모델 한도에 들어가는지 분석 — Paperclip ``analyzeContext`` 포팅."""
    label = (model or "").strip()
    window = resolve_model_context_window(label or None)
    fraction = min(0.95, max(0.40, float(safety_fraction)))

    body = estimate_text_tokens(prompt or "")
    if system:
        body += estimate_text_tokens(system)
    total = body + SUBMISSION_FIXED_OVERHEAD_TOKENS

    allowed = _max_submission_estimate_allowed(window, fraction)
    chunk_budget = _suggested_chunk_budget(window, fraction)
    fits = total <= allowed

    if fits:
        chunks_needed = 1
        recommendation = (
            f"한 번에 처리 가능 — model='{label or 'unknown'}', "
            f"ctx={window}, 제출허용≈{allowed}, 본문={body}"
        )
    else:
        chunks_needed = max(2, -(-body // max(1, chunk_budget)))
        strategy = "분할 권장" if chunks_needed <= 4 else "분할 + 요약 단계 권장"
        recommendation = (
            f"입력 규모 초과 → 약 {chunks_needed}개 청크 ({strategy}) — "
            f"본문={body}, 청크본문≤{chunk_budget}, 제출허용≈{allowed}"
        )

    return PromptAnalysis(
        model=label,
        model_context_window=window,
        effective_submission_budget=allowed,
        chunk_token_budget=chunk_budget,
        total_tokens=total,
        body_tokens=body,
        chunks_needed=chunks_needed,
        fits_context=fits,
        recommendation=recommendation,
    )


_PARAGRAPH_SPLIT_RE = re.compile(r"\r?\n{2,}")
_MD_HEADING_SPLIT_RE = re.compile(r"(?=^#{1,6}\s+\S)", re.MULTILINE)


def _split_paragraphs_with_headings(text: str) -> list[str]:
    """Paperclip ``splitBlocks`` 포팅: 단락 분리 + 큰 단락 내부 markdown 헤딩 분리."""
    t = (text or "").strip()
    if not t:
        return []
    paragraphs = [p.strip() for p in _PARAGRAPH_SPLIT_RE.split(t) if p.strip()]
    if not paragraphs:
        if _MD_HEADING_SPLIT_RE.search(t):
            return [p.strip() for p in _MD_HEADING_SPLIT_RE.split(t) if p.strip()]
        return [ln.strip() for ln in t.split("\n") if ln.strip()]
    out: list[str] = []
    for p in paragraphs:
        if len(p) > 4_000 and _MD_HEADING_SPLIT_RE.search(p):
            for s in _MD_HEADING_SPLIT_RE.split(p):
                s2 = s.strip()
                if s2:
                    out.append(s2)
        else:
            out.append(p)
    return out


@dataclass(frozen=True)
class PromptChunk:
    """chunk_prompt_for_model 결과 원소. Paperclip ``PromptChunk`` 와 동일 의미."""

    index: int
    content: str
    token_count: int
    total_chunks: int


def chunk_prompt_for_model(
    prompt: str,
    *,
    model: str | None = None,
    system: str | None = None,
    safety_fraction: float = INPUT_SAFETY_FRACTION_DEFAULT,
    estimator: Callable[[str], int] | None = None,
    overlap_ratio: float = 0.0,
) -> list[PromptChunk]:
    """
    Paperclip ``chunkPrompt`` 포팅: 단락/헤딩 단위 greedy packing.

    Args:
        overlap_ratio: 0보다 크면 인접 청크 간에 ``add_overlap_to_chunks`` 로 이전 청크 꼬리를
            **이전 청크 연속** 프리픽스와 함께 다음 청크 머리에 삽입한다. 0.0 이면 오버랩 없음(기본).
            상·하한은 ``add_overlap_to_chunks`` 가 ``[0.03, 0.45]`` 로 클램프.
    """
    text = (prompt or "").strip()
    if not text:
        return []

    est = estimator or estimate_text_tokens
    analysis = analyze_prompt_for_model(
        prompt,
        model=model,
        system=system,
        safety_fraction=safety_fraction,
    )

    if analysis.chunks_needed <= 1:
        return [PromptChunk(index=0, content=text, token_count=est(text), total_chunks=1)]

    blocks = _split_paragraphs_with_headings(text)
    if not blocks:
        return [PromptChunk(index=0, content=text, token_count=est(text), total_chunks=1)]

    cap = analysis.chunk_token_budget
    packed: list[str] = []
    cur: list[str] = []
    cur_tok = 0

    for blk in blocks:
        btok = est(blk)
        if not cur and btok > cap:
            sub = _pack_sentences_into_budget(split_sentences(blk), cap, est)
            if sub:
                packed.extend(sub)
            else:
                packed.append(blk)
            continue
        if cur and cur_tok + btok > cap:
            packed.append("\n\n".join(cur))
            cur = [blk]
            cur_tok = btok
        else:
            cur.append(blk)
            cur_tok += btok

    if cur:
        packed.append("\n\n".join(cur))

    packed = [p for p in packed if p.strip()] or [text]

    if overlap_ratio and overlap_ratio > 0 and len(packed) > 1:
        packed = add_overlap_to_chunks(packed, overlap_ratio=float(overlap_ratio))

    total = len(packed)
    return [
        PromptChunk(index=i, content=content, token_count=est(content), total_chunks=total)
        for i, content in enumerate(packed)
    ]


def format_chunk_with_preamble(
    content: str,
    *,
    index: int,
    total_chunks: int,
    note: str | None = None,
) -> str:
    """Paperclip ``execute.ts`` 의 chunkStdin 프리앰블 포팅."""
    if total_chunks <= 1:
        return content
    note_line = f"{note.strip()}\n" if note and note.strip() else ""
    preamble = (
        f"[원본 프롬프트가 컨텍스트 한도를 넘어 {total_chunks}개 청크로 분할되었습니다.]\n"
        f"**청크 {index + 1}/{total_chunks}만** 처리하세요. 다른 청크 내용은 추측하지 마세요.\n"
        f"{note_line}\n---\n\n"
    )
    return f"{preamble}{content}"


def merge_chunk_outputs(
    outputs: Sequence[str | None],
    *,
    delimiter: str = "\n---\n",
) -> str:
    """Paperclip ``mergeChunkAdapterOutputs`` 포팅: ``[Chunk i/N]\\n…`` 결합."""
    total = len(outputs)
    if total == 0:
        return ""
    parts: list[str] = []
    for idx, raw in enumerate(outputs):
        text = (raw or "").strip()
        if not text:
            continue
        parts.append(f"[Chunk {idx + 1}/{total}]\n{text}")
    return delimiter.join(parts)


async def run_chunked_prompt(
    prompt: str,
    runner: Callable[[str], Awaitable[str]],
    *,
    model: str | None = None,
    system: str | None = None,
    safety_fraction: float = INPUT_SAFETY_FRACTION_DEFAULT,
    preamble_note: str | None = None,
    overlap_ratio: float = 0.0,
) -> tuple[str, PromptAnalysis, list[PromptChunk]]:
    """
    Paperclip ``execute.ts`` 의 ``analyzePrompt → chunkPrompt → for-each runAttempt → merge`` 포팅.

    Args:
        overlap_ratio: 인접 청크 간 경계 정보 손실을 완화하기 위한 비율. 0.0 이면 미적용(기본).
            ``CHUNKING_OVERLAP_RATIO`` 환경변수의 권장 기본은 0.15 이지만, 이 API 는
            **호출 측에서 명시적으로 넘긴 값만** 적용해 환경 의존성을 최소화한다.

    Returns:
        (merged_output, analysis, chunks) 튜플. 단일 청크면 merged_output 은 runner 1회 결과 그대로.
    """
    analysis = analyze_prompt_for_model(
        prompt,
        model=model,
        system=system,
        safety_fraction=safety_fraction,
    )
    chunks = chunk_prompt_for_model(
        prompt,
        model=model,
        system=system,
        safety_fraction=safety_fraction,
        overlap_ratio=overlap_ratio,
    )

    if len(chunks) <= 1:
        single_content = chunks[0].content if chunks else prompt
        out = await runner(single_content)
        return (out or "").strip(), analysis, chunks

    outputs: list[str] = []
    for ch in chunks:
        framed = format_chunk_with_preamble(
            ch.content,
            index=ch.index,
            total_chunks=ch.total_chunks,
            note=preamble_note,
        )
        outputs.append(await runner(framed))

    return merge_chunk_outputs(outputs), analysis, chunks


def chunking_metrics_snapshot(
    analysis: "PromptAnalysis",
    chunks: Sequence["PromptChunk"],
    *,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """
    구조화 로그·Prometheus exporter 어댑터용 스냅샷.

    Orchestrator/MEDI/CoOps/ADK 가 한 번의 LLM 호출 단위로 동일한 키 형식을 내보낼 수 있도록
    ``analyze_prompt_for_model`` 결과와 실제 생성된 청크 리스트를 단순 dict 로 직렬화한다.
    Prometheus client (별도 의존)가 있으면 호출 측에서 이 dict 를 ``Gauge.set()`` 에 매핑한다.
    """
    body = max(1, int(getattr(analysis, "body_tokens", 0) or 0))
    chunk_token_sum = sum(int(getattr(c, "token_count", 0) or 0) for c in chunks)
    snap: dict[str, Any] = {
        "chunking_model": getattr(analysis, "model", "") or "",
        "chunking_model_context_window": int(getattr(analysis, "model_context_window", 0) or 0),
        "chunking_submission_budget": int(
            getattr(analysis, "effective_submission_budget", 0) or 0
        ),
        "chunking_chunk_token_budget": int(getattr(analysis, "chunk_token_budget", 0) or 0),
        "chunking_body_tokens_estimated": body,
        "chunking_total_tokens_estimated": int(getattr(analysis, "total_tokens", 0) or 0),
        "chunking_chunks_needed": int(getattr(analysis, "chunks_needed", 0) or 0),
        "chunking_chunks_produced": len(chunks),
        "chunking_fits_context": bool(getattr(analysis, "fits_context", False)),
        # 오버랩이 켜졌으면 본문 토큰 합이 원본보다 커질 수 있다 → 비율로 노출(>=1.0 이면 오버랩).
        "chunking_overlap_inflation_ratio": round(chunk_token_sum / body, 4) if body else 0.0,
        "chunking_recommendation": getattr(analysis, "recommendation", "") or "",
    }
    if extra:
        for k, v in extra.items():
            snap[str(k)] = v
    return snap


def orchestrator_context_compact_enabled() -> bool:
    return os.getenv("ORCH_COMPACT_CONTEXT", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def orchestrator_context_max_tokens() -> int:
    raw = os.getenv("ORCH_CONTEXT_MAX_TOKENS", "14000").strip()
    try:
        return max(2000, int(raw))
    except ValueError:
        return 14_000


# 대화 목록 접기에 사용되는 context 키 (prepare_orchestrator_context + 공개 API 공통)
CONVERSATION_LIST_KEYS: tuple[str, ...] = (
    "messages",
    "history",
    "conversation",
    "conversation_history",
    "chat_history",
    "turns",
)


def chunking_max_parallel() -> int:
    raw = os.getenv("CHUNKING_MAX_PARALLEL", "4").strip()
    try:
        return max(1, min(32, int(raw)))
    except ValueError:
        return 4


def chunking_overlap_ratio() -> float:
    raw = os.getenv("CHUNKING_OVERLAP_RATIO", "0.15").strip()
    try:
        return float(min(0.45, max(0.03, float(raw))))
    except ValueError:
        return 0.15


def orchestrator_history_max_tokens() -> int:
    raw = os.getenv("CHUNKING_HISTORY_MAX_TOKENS", "8000").strip()
    try:
        return max(512, int(raw))
    except ValueError:
        return 8000


def orchestrator_conversation_recent_turns() -> int:
    raw = os.getenv("CHUNKING_CONVERSATION_RECENT_TURNS", "5").strip()
    try:
        return max(1, min(50, int(raw)))
    except ValueError:
        return 5


def _turn_sequence_to_messages(seq: Sequence[Any]) -> list[Message | Mapping[str, Any]]:
    out: list[Message | Mapping[str, Any]] = []
    for item in seq:
        if isinstance(item, Message):
            out.append(item)
        elif isinstance(item, Mapping):
            role = str(item.get("role") or "user")
            content = str(item.get("content") or "")
            out.append(Message(role=role, content=content))
        else:
            out.append(Message(role="user", content=str(item)))
    return out


def _estimate_turn_list_tokens(seq: Sequence[Any]) -> int:
    return estimate_messages_tokens(_turn_sequence_to_messages(seq))


def _truncate_old_turns(
    items: list[Any],
    *,
    recent_keep: int,
) -> tuple[list[Any], str]:
    rk = max(1, recent_keep)
    old = items[:-rk]
    recent = items[-rk:]
    approx_old = _estimate_turn_list_tokens(old)

    structured = False
    for x in items:
        if isinstance(x, Message):
            structured = True
            break
        if isinstance(x, Mapping) and ("role" in x or "content" in x):
            structured = True
            break

    if structured:
        stub: Any = Message(
            role="system",
            content=(
                f"[prior_turns_truncated exchanges={len(old)} approx_tokens_est={approx_old} "
                f"strategy=deterministic_recent_{rk}]"
            ),
        )
    else:
        stub = (
            f"[prior_turns_truncated exchanges={len(old)} approx_tokens_est={approx_old} "
            f"strategy=deterministic_recent_{rk}]"
        )
    trimmed = [stub] + recent
    new_tok = _estimate_turn_list_tokens(trimmed)
    meta = (
        f"trunc_exchanges_was={len(items)}→{len(trimmed)} "
        f"tok_was~{_estimate_turn_list_tokens(items)}→~{new_tok}"
    )
    return trimmed, meta


def summarize_conversation_history(
    messages: Sequence[Any],
    *,
    max_history_tokens: int | None = None,
    recent_keep: int | None = None,
) -> list[Any]:
    """
    멀티턴 누적을 **토큰 예산 안**으로 맞추는 결정적(비‑LLM) 레이어.
    초과 시 마지막 ``recent_keep`` 턴만 원문 보존하고, 그 이전 턴은 스텁 한 건으로 접는다.

    LLM 요약이 필요하면 이 함수 반환 문자열 상단에 사용자 정의 처리를 두거나 별도 파이프를 구성한다.
    """
    seq = list(messages)
    if not seq:
        return seq
    rk = orchestrator_conversation_recent_turns() if recent_keep is None else max(1, recent_keep)
    budget = orchestrator_history_max_tokens() if max_history_tokens is None else max(200, max_history_tokens)

    if _estimate_turn_list_tokens(seq) <= budget:
        return seq
    if len(seq) <= rk:
        return seq

    shrink_rk = rk
    last_out, _ = _truncate_old_turns(seq, recent_keep=shrink_rk)
    tries = 0
    while _estimate_turn_list_tokens(last_out) > budget and tries < 48 and shrink_rk > 0:
        shrink_rk = max(1, shrink_rk - 1)
        if len(seq) <= shrink_rk:
            break
        last_out, _ = _truncate_old_turns(seq, recent_keep=shrink_rk)
        tries += 1

    return last_out


def prune_conversation_lists_for_budget(ctx: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    """CONVERSATION_LIST_KEYS 해당 list 만 대화 접기 규칙 적용한다."""
    out = dict(ctx)
    metas: list[str] = []
    budget = orchestrator_history_max_tokens()

    for key in CONVERSATION_LIST_KEYS:
        if key not in out:
            continue
        val = out[key]
        if not isinstance(val, list) or not val:
            continue
        if _estimate_turn_list_tokens(val) <= budget:
            continue
        rk_base = orchestrator_conversation_recent_turns()
        if len(val) <= rk_base:
            continue
        shrink_rk = rk_base
        new_lst, meta = _truncate_old_turns(val, recent_keep=shrink_rk)
        tries = 0
        while _estimate_turn_list_tokens(new_lst) > budget and tries < 24 and shrink_rk > 0:
            shrink_rk = max(1, shrink_rk - 1)
            if len(val) <= shrink_rk:
                break
            new_lst, meta = _truncate_old_turns(val, recent_keep=shrink_rk)
            tries += 1
        out[key] = new_lst
        metas.append(f"{key}({meta})")

    suffix = ";".join(metas)
    return out, suffix


def prepare_orchestrator_context(task: str, context: dict | None) -> dict[str, Any]:
    """
    ``Orchestrator.execute(task, context)`` 직전 호출용.
    ORCH_COMPACT_CONTEXT=1 이면 context JSON 크기 목표까지 축소.
    """
    ctx_in = dict(context or {})
    compact = orchestrator_context_compact_enabled()
    tok_pre = estimate_orchestrator_input_tokens(task, ctx_in)
    prune_meta = ""

    if compact:
        ctx_in, prune_meta = prune_conversation_lists_for_budget(ctx_in)

    tok_post_prune = estimate_orchestrator_input_tokens(task, ctx_in)

    if not compact:
        return ctx_in

    t0 = time.perf_counter()

    shrunk = compact_context_for_budget(
        ctx_in,
        max_payload_tokens=orchestrator_context_max_tokens(),
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    tok_compact = estimate_orchestrator_input_tokens(task, shrunk)
    ratio = round(tok_compact / max(1.0, float(tok_pre)), 6)

    logger.info(
        "chunking_ctx_compact elapsed_ms=%.3f orch_tokens_est pre=%s post_prune=%s post_compact=%s "
        "ratio_vs_pre=%s conv_pruned=%s",
        elapsed_ms,
        tok_pre,
        tok_post_prune,
        tok_compact,
        ratio,
        prune_meta or "noop",
    )
    return shrunk


async def parallel_map_chunks(
    chunks: Sequence[str],
    processor: Callable[[str], Awaitable[str]],
    *,
    max_concurrency: int | None = None,
) -> list[str]:
    if not chunks:
        return []
    conc = chunking_max_parallel() if max_concurrency is None else max(1, max_concurrency)
    sem = asyncio.Semaphore(conc)

    async def one(c: str) -> str:
        async with sem:
            return await processor(c)

    return list(await asyncio.gather(*[one(c) for c in chunks]))


def merge_chunks_sequential(chunks: Sequence[str], *, delimiter: str = "\n---\n") -> str:
    return delimiter.join(str(c).strip() for c in chunks if str(c).strip())


def merge_chunks_recursive_pairs(chunks: Sequence[str]) -> str:
    parts = [str(c).strip() for c in chunks if str(c).strip()]
    if not parts:
        return ""
    while len(parts) > 1:
        nxt: list[str] = []
        i = 0
        while i < len(parts):
            if i + 1 < len(parts):
                nxt.append(parts[i] + "\n\n" + parts[i + 1])
                i += 2
            else:
                nxt.append(parts[i])
                i += 1
        parts = nxt
    return parts[0]


# 통합 병합 진입점 — Python 기존 강점(`sequential`/`recursive_pair`) + Paperclip 인덱스 형식.
_MERGE_STRATEGY_ALIASES: dict[str, str] = {
    "sequential": "sequential",
    "sequential_concat": "sequential",
    "concat": "sequential",
    "recursive": "recursive_pair",
    "recursive_pair": "recursive_pair",
    "recursive_pairs": "recursive_pair",
    "pairwise": "recursive_pair",
    "indexed": "indexed",
    "paperclip": "indexed",
    "chunked": "indexed",
}


def merge_chunks(
    chunks: Sequence[str | None],
    *,
    strategy: str = "sequential",
    delimiter: str = "\n---\n",
) -> str:
    """
    병합 전략 통합 헬퍼. 기본은 ``sequential``(기존 Python 표준 동작).

    Args:
        strategy: ``sequential`` | ``recursive_pair`` | ``indexed`` (또는 동의어).
            - ``sequential``: ``merge_chunks_sequential`` 위임.
            - ``recursive_pair``: ``merge_chunks_recursive_pairs`` 위임(결합 트리 깊이 완화).
            - ``indexed``: ``merge_chunk_outputs`` 위임(`[Chunk i/N]\\n…` Paperclip 포맷).
    """
    cleaned: list[str] = [str(c) for c in chunks if c is not None]
    key = (strategy or "").strip().lower()
    resolved = _MERGE_STRATEGY_ALIASES.get(key)
    if resolved == "sequential":
        return merge_chunks_sequential(cleaned, delimiter=delimiter)
    if resolved == "recursive_pair":
        return merge_chunks_recursive_pairs(cleaned)
    if resolved == "indexed":
        return merge_chunk_outputs(cleaned, delimiter=delimiter)
    raise ValueError(
        f"Unknown merge strategy: {strategy!r}. 지원: sequential | recursive_pair | indexed"
    )


def compress_conversation_history(
    messages: Sequence[Any],
    *,
    max_tokens: int = 12_000,
    preserve_recent: int = 5,
) -> str:
    """
    멀티턴 압축의 **문자열 반환 형** API (Python 기존 강점 유지 + 호출 편의).

    ``summarize_conversation_history`` 가 만든 리스트(최근 N턴 원문 + 이전 턴 접기 스텁)를
    ``[role]\\n내용`` 라벨로 결합한 단일 프롬프트 문자열로 직렬화한다.
    Orchestrator/agents 외부에서 LLMRequest 의 ``system`` 으로 바로 주입할 때 편리하다.
    """
    if not messages:
        return ""
    folded = summarize_conversation_history(
        messages,
        max_history_tokens=max_tokens,
        recent_keep=preserve_recent,
    )
    parts: list[str] = []
    for m in folded:
        role = ""
        content = ""
        if isinstance(m, Mapping):
            role = str(m.get("role") or "").strip()
            content = str(m.get("content") or "")
        elif isinstance(m, Message):
            role = m.role
            content = m.content or ""
        else:
            content = str(m)
        if not content.strip():
            continue
        if role:
            parts.append(f"[{role}]\n{content.strip()}")
        else:
            parts.append(content.strip())
    return "\n\n".join(parts)


async def identity_chunk(s: str) -> str:
    return s.strip()


def context_chunked(
    *,
    max_input_tokens: int = 8192,
    merge_strategy: str = "sequential_concat",
    max_parallel_chunks: int | None = None,
    reserve_tokens_for_system: int = 384,
    turn_delimiter_regex: str | None = r"\n-{3,}\n",
    prompt_param: str = "prompt",
    chunk_processor: Callable[[str], Awaitable[str]] | None = None,
    use_overlap: bool = False,
    overlap_ratio: float | None = None,
    overlap_chars_min: int = 120,
    prioritize_medical_turns: bool = False,
    priority_keywords: Sequence[str] | None = None,
):
    """
    kwargs에 ``prompt_param`` 문자열이 있고 예산 초과 시 청킹·병합 후 재호출.

    prioritize_medical_turns: 증상/검사 등 턴 우선 순서 재배열(경계 분할 안전 확대 목적).

    오버랩: ``use_overlap=True`` 이면 ``overlap_ratio`` 를 쓰고, ``None`` 이면
    ``CHUNKING_OVERLAP_RATIO`` 환경값(기본 0.15)을 쓴다. 병렬 청크는 ``max_parallel_chunks=None`` 일 때 ``CHUNKING_MAX_PARALLEL``.
    """

    def decorator(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @functools.wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            prompt_val = kwargs.get(prompt_param)
            if not isinstance(prompt_val, str):
                return await fn(*args, **kwargs)

            system_kw = kwargs.get("system")
            system_str = system_kw if isinstance(system_kw, str) else None

            payload = estimate_llm_chat_payload_tokens(
                prompt=prompt_val,
                system=system_str,
            )
            if payload <= max_input_tokens:
                return await fn(*args, **kwargs)

            inner_budget = max(256, max_input_tokens - reserve_tokens_for_system)

            delim = turn_delimiter_regex or ""
            if delim and re.search(delim, prompt_val):
                turns = [
                    p.strip()
                    for p in re.split(delim, prompt_val)
                    if p.strip()
                ]
            else:
                turns = [prompt_val.strip()] if prompt_val.strip() else []

            if prioritize_medical_turns:
                pkw = (
                    tuple(priority_keywords)
                    if priority_keywords is not None
                    else DEFAULT_PRIORITY_KEYWORDS
                )
                turns = prioritize_turn_strings(turns, pkw)

            t0 = time.perf_counter()

            eff_overlap = (
                chunking_overlap_ratio()
                if overlap_ratio is None
                else float(min(0.45, max(0.03, float(overlap_ratio))))
            )

            if use_overlap:
                chunks = semantic_chunking_with_overlap(
                    turns,
                    max_tokens_per_chunk=inner_budget,
                    overlap_ratio=eff_overlap,
                    overlap_chars_min=overlap_chars_min,
                    estimator=estimate_text_tokens,
                )
            else:
                chunks = semantic_chunking(
                    turns,
                    max_tokens_per_chunk=inner_budget,
                    estimator=estimate_text_tokens,
                )

            processor = chunk_processor or identity_chunk
            t_proc0 = time.perf_counter()
            processed = await parallel_map_chunks(
                chunks,
                processor,
                max_concurrency=max_parallel_chunks,
            )
            t_after_parallel = time.perf_counter()
            merged = (
                merge_chunks_recursive_pairs(processed)
                if merge_strategy == "recursive_pair"
                else merge_chunks_sequential(processed)
            )
            total_ms = (time.perf_counter() - t0) * 1000.0
            parallel_ms = (t_after_parallel - t_proc0) * 1000.0

            _qs = chunking_quality_score(prompt_val, merged)
            logger.info(
                "context_chunked chunks_in=%s chunks_after_proc=%s parallel_ms=%.3f "
                "merge=%s use_overlap=%s overlap_ratio_eff=%s max_parallel=%s "
                "quality_approx=%s total_ms=%.3f",
                len(chunks),
                len(processed),
                parallel_ms,
                merge_strategy,
                use_overlap,
                eff_overlap if use_overlap else None,
                max_parallel_chunks if max_parallel_chunks is not None else chunking_max_parallel(),
                _qs,
                total_ms,
            )
            logger.debug(
                "context_chunked_quality %s",
                _qs,
            )

            merged_kw = dict(kwargs)
            merged_kw[prompt_param] = merged
            return await fn(*args, **merged_kw)

        return wrapper

    return decorator


# ════════════════════════════════════════════════════════════
# Step 6 — LLM 요약 레이어 (옵션 / 기본 OFF)
# ════════════════════════════════════════════════════════════
# Step 3 의 결정적 trim 위에 LLM 1-call 요약을 옵션으로 끼워 넣는다.
# 결정 원칙:
#   1) 기본 OFF — LLM_SUMMARY_LAYER_ENABLED=1 이어야 활성
#   2) 결정적 trim 결과를 항상 fallback 으로 보존 — LLM 호출이 실패해도 거동 유지
#   3) "정말로 의미있는 압축이 필요한 경우만" 호출 — fallback='chars' 트리거 기본값
#   4) Summarizer 의존성을 외부 주입(Protocol)로 두어 unit test 가 mocking 쉽다
#
# 관련 환경변수 (env-reference 와 일치):
#   - LLM_SUMMARY_LAYER_ENABLED : 0/1, 기본 0
#   - LLM_SUMMARY_TRIGGER       : "chars" | "sentence" | "always", 기본 "chars"
#   - LLM_SUMMARY_MAX_TOKENS    : 요약 결과 토큰 상한, 기본 512
#   - LLM_SUMMARY_MODEL         : LM Studio 모델 ID, 기본 google/gemma-4-e4b (light)
#   - LLM_SUMMARY_BASE_URL      : LM Studio 베이스 URL, 기본 호스트 LM Studio
#   - LLM_SUMMARY_TIMEOUT       : HTTP timeout 초, 기본 30


def llm_summary_layer_enabled() -> bool:
    """``LLM_SUMMARY_LAYER_ENABLED=1`` 일 때만 True (기본 OFF — 점진적 도입)."""
    return os.getenv("LLM_SUMMARY_LAYER_ENABLED", "0").strip() in {"1", "true", "TRUE", "yes"}


def llm_summary_max_tokens(default: int = 512) -> int:
    try:
        return max(64, int(os.getenv("LLM_SUMMARY_MAX_TOKENS", str(default))))
    except (TypeError, ValueError):
        return default


def llm_summary_trigger() -> str:
    """LLM 요약을 시도할 조건: ``chars`` | ``sentence`` | ``always`` (기본 ``chars``)."""
    raw = (os.getenv("LLM_SUMMARY_TRIGGER", "chars") or "").strip().lower()
    return raw if raw in {"chars", "sentence", "always"} else "chars"


class LLMSummarizer:
    """LLM 1-call 요약기 Protocol.

    실제 구현체는 ``LMStudioJSONSummarizer`` 또는 호출자가 만드는 Mock.
    Protocol 자체를 Python ``typing.Protocol`` 로 두지 않고 클래스로 둔 이유:
    instanceof 검사 + duck typing 양쪽을 동시에 지원하기 위함.
    """

    async def summarize(
        self,
        *,
        text: str,
        max_tokens: int,
        hint: str | None = None,
    ) -> str:
        raise NotImplementedError


class NoopLLMSummarizer(LLMSummarizer):
    """LLM 호출 없이 입력 그대로 반환 (테스트/안전 fallback)."""

    async def summarize(
        self, *, text: str, max_tokens: int, hint: str | None = None
    ) -> str:
        return text or ""


class LMStudioJSONSummarizer(LLMSummarizer):
    """LM Studio /v1/chat/completions 어댑터 (httpx 사용).

    의존성 ``httpx`` 가 없으면 RuntimeError. dev 환경 기본값으로 컨테이너 호스트의
    LM Studio (``host.docker.internal:8000/v1``) 와 ``google/gemma-4-e4b`` (light)
    를 쓰며, 환경변수로 모두 덮어쓸 수 있다.
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        api_key: str | None = None,
    ) -> None:
        self.model = model or os.getenv("LLM_SUMMARY_MODEL", "google/gemma-4-e4b")
        self.base_url = (
            base_url
            or os.getenv("LLM_SUMMARY_BASE_URL", "http://host.docker.internal:8000/v1")
        ).rstrip("/")
        try:
            self.timeout = float(timeout if timeout is not None else os.getenv("LLM_SUMMARY_TIMEOUT", "30"))
        except (TypeError, ValueError):
            self.timeout = 30.0
        self.api_key = api_key or os.getenv("LLM_SUMMARY_API_KEY", "lm-studio")

    async def summarize(
        self, *, text: str, max_tokens: int, hint: str | None = None
    ) -> str:
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError(
                "LMStudioJSONSummarizer 는 httpx 가 필요합니다 "
                "(pip install httpx)"
            ) from exc

        system = (
            "당신은 의미 손실 없이 입력 텍스트를 압축하는 요약 도우미다.\n"
            "- 사실/숫자/명사/도메인 용어는 우선 보존한다.\n"
            "- 단순 반복/패딩/장식 문구는 한 줄로 압축한다.\n"
            "- 출력은 평문(prose) 본문만 — 인사·사족·번호 매김 금지."
        )
        if hint:
            system += f"\n- 컨텍스트 힌트: {hint}"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(
                f"{self.base_url}/chat/completions",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": text},
                    ],
                    "max_tokens": int(max_tokens),
                    "temperature": 0.2,
                },
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            r.raise_for_status()
            data = r.json()
            choices = data.get("choices") or []
            if not choices:
                return ""
            msg = (choices[0] or {}).get("message") or {}
            return str(msg.get("content") or "").strip()


def _should_attempt_llm_summary(fallback: str) -> bool:
    """현재 trigger 정책과 trim 결과의 fallback 값으로 LLM 요약 시도 여부 판정."""
    trig = llm_summary_trigger()
    if trig == "always":
        return True
    if trig == "sentence":
        return fallback in {"sentence", "chars"}
    return fallback == "chars"


async def trim_text_with_llm_summary(
    text: str,
    *,
    max_tokens: int,
    summarizer: LLMSummarizer | None = None,
    head_ratio: float = 0.7,
    tail_ratio: float = 0.25,
    hint: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """결정적 trim 위에 LLM 1-call 요약을 옵션으로 끼우는 보호된 헬퍼.

    동작:
        1. ``trim_text_to_token_budget`` 으로 먼저 절단 (보장된 결정적 결과 확보)
        2. ``LLM_SUMMARY_LAYER_ENABLED=1`` 이고 ``summarizer`` 가 주어지면
           ``LLM_SUMMARY_TRIGGER`` 기준으로 요약 시도
        3. 성공 시 요약 결과를 ``max_tokens`` 안으로 다시 trim 해서 반환
        4. 실패/비활성/예산 통과 시 결정적 trim 결과 그대로 반환 (graceful degrade)

    Returns:
        ``(text, info)`` — info 는 ``trim_text_to_token_budget`` 의 키에
        ``llm_summary_attempted`` / ``llm_summary_used`` / ``llm_summary_error`` /
        ``llm_summary_pre_tokens`` 키를 추가한다.
    """
    trimmed, info = trim_text_to_token_budget(
        text,
        max_tokens=max_tokens,
        head_ratio=head_ratio,
        tail_ratio=tail_ratio,
    )
    info.setdefault("llm_summary_attempted", False)
    info.setdefault("llm_summary_used", False)
    info.setdefault("llm_summary_error", "")
    info.setdefault("llm_summary_pre_tokens", info.get("post_tokens", 0))

    if not llm_summary_layer_enabled() or summarizer is None:
        return trimmed, info
    if not info.get("trimmed"):
        return trimmed, info
    if not _should_attempt_llm_summary(str(info.get("fallback", "none"))):
        return trimmed, info

    target = max(64, min(int(max_tokens), llm_summary_max_tokens()))
    info["llm_summary_attempted"] = True
    try:
        summary = await summarizer.summarize(text=text, max_tokens=target, hint=hint)
    except Exception as exc:
        info["llm_summary_error"] = type(exc).__name__
        logger.warning(
            "trim_text_with_llm_summary: summarizer 실패(%s) — 결정적 결과 유지",
            exc,
        )
        return trimmed, info

    summary = (summary or "").strip()
    if not summary:
        info["llm_summary_error"] = "empty_response"
        return trimmed, info

    post = estimate_text_tokens(summary)
    if post > max_tokens:
        summary, _ = trim_text_to_token_budget(summary, max_tokens=max_tokens)
        post = estimate_text_tokens(summary)

    info.update(
        {
            "llm_summary_used": True,
            "post_tokens": post,
            "dropped_tokens": max(0, int(info.get("pre_tokens", 0)) - post),
            "trimmed": True,
            "fallback": "llm_summary",
        }
    )
    return summary, info


async def compress_conversation_history_with_llm_summary(
    messages: Sequence[Any],
    *,
    max_tokens: int = 12_000,
    preserve_recent: int = 5,
    summarizer: LLMSummarizer | None = None,
    hint: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """``compress_conversation_history`` 의 LLM 요약 확장.

    동작:
        1. ``compress_conversation_history`` 로 결정적 직렬화 (최근 N턴 보존 + 스텁)
        2. 결과 토큰 추정이 ``max_tokens`` 이하면 결정적 결과 그대로 반환
        3. 초과 + 환경 ON + ``summarizer`` 있으면 LLM 1-call 요약 시도
        4. 실패 시 결정적 결과를 ``trim_text_to_token_budget`` 로 절단해 반환

    Returns:
        ``(text, info)`` — info 키:
            ``pre_tokens`` / ``post_tokens`` / ``llm_summary_attempted`` /
            ``llm_summary_used`` / ``llm_summary_error`` / ``fallback``.
    """
    raw = compress_conversation_history(
        messages,
        max_tokens=max_tokens,
        preserve_recent=preserve_recent,
    )
    pre = estimate_text_tokens(raw)
    info: dict[str, Any] = {
        "pre_tokens": pre,
        "post_tokens": pre,
        "trimmed": False,
        "fallback": "deterministic",
        "llm_summary_attempted": False,
        "llm_summary_used": False,
        "llm_summary_error": "",
    }
    if pre <= max_tokens:
        return raw, info

    if not llm_summary_layer_enabled() or summarizer is None:
        # 환경 OFF — 결정적 결과를 최종 trim 으로만 강제 압축
        trimmed, t_info = trim_text_to_token_budget(raw, max_tokens=max_tokens)
        info.update(
            post_tokens=t_info["post_tokens"],
            trimmed=t_info["trimmed"],
            fallback=f"deterministic_trim_{t_info['fallback']}",
        )
        return trimmed, info

    target = max(64, min(int(max_tokens), llm_summary_max_tokens()))
    info["llm_summary_attempted"] = True
    try:
        summary = await summarizer.summarize(
            text=raw,
            max_tokens=target,
            hint=hint
            or "Multi-turn conversation summary — preserve recent turns and key intent.",
        )
    except Exception as exc:
        info["llm_summary_error"] = type(exc).__name__
        trimmed, t_info = trim_text_to_token_budget(raw, max_tokens=max_tokens)
        info.update(
            post_tokens=t_info["post_tokens"],
            trimmed=t_info["trimmed"],
            fallback=f"deterministic_trim_{t_info['fallback']}",
        )
        return trimmed, info

    summary = (summary or "").strip()
    if not summary:
        info["llm_summary_error"] = "empty_response"
        trimmed, t_info = trim_text_to_token_budget(raw, max_tokens=max_tokens)
        info.update(
            post_tokens=t_info["post_tokens"],
            trimmed=t_info["trimmed"],
            fallback=f"deterministic_trim_{t_info['fallback']}",
        )
        return trimmed, info

    post = estimate_text_tokens(summary)
    if post > max_tokens:
        summary, _ = trim_text_to_token_budget(summary, max_tokens=max_tokens)
        post = estimate_text_tokens(summary)
    info.update(
        {
            "llm_summary_used": True,
            "post_tokens": post,
            "trimmed": True,
            "fallback": "llm_summary",
        }
    )
    return summary, info


__all__ = [
    "CONVERSATION_LIST_KEYS",
    "DEFAULT_PRIORITY_KEYWORDS",
    "add_overlap_to_chunks",
    "chunking_max_parallel",
    "chunking_overlap_ratio",
    "chunking_quality_score",
    "compact_context_for_budget",
    "context_chunked",
    "estimate_messages_tokens",
    "estimate_text_tokens",
    "estimate_llm_chat_payload_tokens",
    "estimate_llm_request_tokens",
    "estimate_orchestrator_input_tokens",
    "identity_chunk",
    "merge_chunks_recursive_pairs",
    "merge_chunks_sequential",
    "orchestrator_context_compact_enabled",
    "orchestrator_context_max_tokens",
    "orchestrator_conversation_recent_turns",
    "orchestrator_history_max_tokens",
    "parallel_map_chunks",
    "prepare_orchestrator_context",
    "prioritize_turn_strings",
    "semantic_chunking",
    "semantic_chunking_with_overlap",
    "split_sentences",
    "summarize_conversation_history",
    "LLMSummarizer",
    "LMStudioJSONSummarizer",
    "NoopLLMSummarizer",
    "compress_conversation_history_with_llm_summary",
    "llm_summary_layer_enabled",
    "llm_summary_max_tokens",
    "llm_summary_trigger",
    "trim_text_with_llm_summary",
]
