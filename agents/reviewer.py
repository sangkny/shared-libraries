# shared-libraries/agents/reviewer.py
"""
ReviewerAgent — Ontology 기반 검증
- 모델: HEAVY (gemma-4-26b-a4b) — 고품질 추론
- 역할: GeneratorAgent 결과물을 OntologyValidator로 검증 + LLM 리뷰
- 출력: ReviewResult (passed/failed + 피드백)

컨텍스트 누적 가드 (book §16.10/§16.11 — 청킹 도입 Step 3):
    Orchestrator 는 동일한 ``task`` 를 Generator·Reviewer 양쪽에 그대로 전달한다.
    Reviewer 는 그 위에 ``generated`` (이미 1500자 cap) + Ontology 섹션 + 도메인 기준
    + 시스템 프롬프트 + LLM ``max_tokens=1024`` 응답 예약을 모두 합산하므로,
    LM Studio 호스트 모델의 실제 컨텍스트 한도(예: 8K cap)를 초과하기 쉽다.
    여기서는 ``task`` 만 ``REVIEWER_TASK_MAX_TOKENS`` (기본 4096) 안으로 절단하고
    ``medi_reviewer_context`` 한 줄 메트릭 로그를 흘려보낸다. 거동(검증 의도)은 보존된다.
"""
import os
from dataclasses import dataclass, field
from typing import Any
from llm.base import ModelRole
from ontology.base import OntologyDomain, ValidationResult
from ontology.validator import OntologyValidator
from .base import BaseAgent, AgentType, AgentResult
from .context_chunking import (
    chunking_metrics_snapshot,
    analyze_prompt_for_model,
    chunk_prompt_for_model,
    estimate_text_tokens,
    trim_text_to_token_budget,
    # Step 6 — LLM 요약 레이어 (옵션 / 기본 OFF)
    LMStudioJSONSummarizer,
    llm_summary_layer_enabled,
    trim_text_with_llm_summary,
)


def _reviewer_task_budget_tokens() -> int:
    """``REVIEWER_TASK_MAX_TOKENS`` 환경변수 — 기본 600.

    LM Studio 가 모델을 약 1.5K~2K 컨텍스트로 로딩하는 운영 현실(현장 측정 결과)을
    반영해 Reviewer 의 ``task`` 부분 토큰 상한을 보수적으로 잡는다. 운영자가
    더 큰 모델 컨텍스트(예: 8K, 16K)로 로딩했다면 이 값을 환경변수로 늘려도 된다.
    정적 비용(generated ≈140 + criteria 300 + system 54 + format 50 + 응답 예약)을
    고려하면 1.5K 컨텍스트에서도 task 600 토큰까지 안전하게 들어간다.
    """
    try:
        raw = os.getenv("REVIEWER_TASK_MAX_TOKENS", "400")
        return max(150, int(raw))
    except (TypeError, ValueError):
        return 400


def _reviewer_response_max_tokens() -> int:
    """``REVIEWER_RESPONSE_MAX_TOKENS`` 환경변수 — 기본 192.

    Reviewer 응답은 ``VERDICT/FEEDBACK/IMPROVEMENTS`` 한두 문장이면 충분하다.
    기존 1024 는 응답 예약으로 컨텍스트 예산을 크게 잡아먹고 있었다.
    """
    try:
        raw = os.getenv("REVIEWER_RESPONSE_MAX_TOKENS", "192")
        return max(64, int(raw))
    except (TypeError, ValueError):
        return 192


def _reviewer_generated_preview_chars() -> int:
    """``REVIEWER_GENERATED_PREVIEW_CHARS`` 환경변수 — 기본 400.

    Reviewer 가 검토할 ``generated`` 결과물의 미리보기 길이(문자). 기존 1500자는
    한국어 기준 약 500 토큰을 매번 차지하므로 작은 컨텍스트 모델에서 누적의
    주요 원인이 된다. 400자(약 140 토큰)로 줄여도 검증 의도는 충분히 표현되며,
    운영자가 더 큰 컨텍스트 모델을 쓰면 이 값을 환경변수로 늘릴 수 있다.
    """
    try:
        raw = os.getenv("REVIEWER_GENERATED_PREVIEW_CHARS", "300")
        return max(120, int(raw))
    except (TypeError, ValueError):
        return 300


@dataclass
class ReviewResult:
    """ReviewerAgent 출력"""
    passed:            bool
    feedback:          str              # FixerAgent에 전달할 피드백
    ontology_result:   ValidationResult | None = None
    llm_review:        str = ""         # LLM 리뷰 내용
    improvement_hints: list[str] = field(default_factory=list)

    def to_fixer_prompt(self) -> str:
        """FixerAgent에 전달할 수정 요청 프롬프트"""
        errors = ""
        if self.ontology_result and self.ontology_result.errors:
            error_list = "\n".join(
                f"- [{e.code}] {e.message}"
                + (f" → {e.suggestion}" if e.suggestion else "")
                for e in self.ontology_result.errors
            )
            errors = f"\nOntology 검증 오류:\n{error_list}"

        hints = ""
        if self.improvement_hints:
            hints = "\n개선 방향:\n" + "\n".join(
                f"- {h}" for h in self.improvement_hints
            )
        return f"{self.feedback}{errors}{hints}"


class ReviewerAgent(BaseAgent):
    """
    Ontology 기반 검증 + LLM 리뷰를 수행하는 Agent

    2단계 검증:
    1. OntologyValidator — 규칙 기반 자동 검증 (빠름)
    2. LLM 리뷰 — 의미/품질 검증 (HEAVY 모델)

    사용법:
        reviewer = ReviewerAgent(domain=OntologyDomain.MEDICAL)
        result   = await reviewer.run(
            "검증할 내용",
            context={"generated": generator_result.output}
        )
        review = result.output  # ReviewResult
        if review.passed:
            # 승인
        else:
            # FixerAgent로 전달
    """

    def __init__(self, *args, ontology_rules: dict[str, Any] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._validator = OntologyValidator(
            domain=self.domain,
            rules=ontology_rules,
        )

    @property
    def agent_type(self) -> AgentType:
        return AgentType.REVIEWER

    @property
    def model_role(self) -> ModelRole:
        return ModelRole.HEAVY  # 고품질 추론 — 26B 모델

    async def run(
        self,
        task:    str,
        context: dict[str, Any] | None = None,
    ) -> AgentResult:
        self.log.info(f"[{self.task_id}] ReviewerAgent 시작 (HEAVY 모델)")
        ctx       = context or {}
        iteration = ctx.get("iteration", 0)
        generated = ctx.get("generated", task)  # 검증할 결과물

        # ── 컨텍스트 누적 가드 (book §16.11 — Step 3) ────────────────────
        # task 만 토큰 예산 안으로 절단. generated/criteria/system 은 그대로 둔다.
        # 거동은 보존하면서 ``Context size exceeded`` 회귀를 막는다.
        _budget = _reviewer_task_budget_tokens()
        try:
            _model_label = getattr(self.llm, "heavy_model", "") or "gemma-4-26b-a4b"
            _orig_task_tokens = estimate_text_tokens(task)
            _trim_info: dict[str, Any] | None = None
            if _orig_task_tokens > _budget:
                # Step 6 — LLM_SUMMARY_LAYER_ENABLED=1 이고 LM Studio 가 닿으면
                # 결정적 trim 위에 LLM 1-call 요약을 옵션으로 끼운다. 환경 OFF
                # 또는 LLM 호출 실패 시 결정적 trim 결과를 그대로 사용한다.
                _summarizer = (
                    LMStudioJSONSummarizer()
                    if llm_summary_layer_enabled()
                    else None
                )
                task, _trim_info = await trim_text_with_llm_summary(
                    task,
                    max_tokens=_budget,
                    summarizer=_summarizer,
                    hint=f"Reviewer task — domain={getattr(self.domain, 'value', self.domain)}",
                )
                self.log.info(
                    "[%s] reviewer_task_trimmed model=%s pre_tokens=%d post_tokens=%d "
                    "dropped=%d fallback=%s budget=%d llm_summary_used=%s "
                    "llm_summary_attempted=%s llm_summary_error=%s "
                    "llm_summary_retry_count=%d",
                    self.task_id,
                    _model_label,
                    _trim_info["pre_tokens"],
                    _trim_info["post_tokens"],
                    _trim_info["dropped_tokens"],
                    _trim_info["fallback"],
                    _trim_info["budget"],
                    _trim_info.get("llm_summary_used", False),
                    _trim_info.get("llm_summary_attempted", False),
                    _trim_info.get("llm_summary_error", ""),
                    int(_trim_info.get("llm_summary_retry_count", 0) or 0),
                )

            _analysis = analyze_prompt_for_model(task, model=_model_label)
            _chunks_for_metric = (
                chunk_prompt_for_model(task, model=_model_label)
                if not _analysis.fits_context
                else []
            )
            self.log.info(
                "medi_reviewer_context",
                extra=chunking_metrics_snapshot(
                    _analysis,
                    _chunks_for_metric,
                    extra={
                        "flow": "agent_reviewer",
                        "task_id": str(self.task_id),
                        "iteration": int(iteration),
                        "domain": getattr(self.domain, "value", str(self.domain)),
                        "trim_pre_tokens": (_trim_info or {}).get(
                            "pre_tokens", _orig_task_tokens
                        ),
                        "trim_post_tokens": (_trim_info or {}).get(
                            "post_tokens", _orig_task_tokens
                        ),
                        "trim_dropped_tokens": (_trim_info or {}).get(
                            "dropped_tokens", 0
                        ),
                        "trim_fallback": (_trim_info or {}).get("fallback", "none"),
                        "trim_applied": _trim_info is not None,
                        "llm_summary_used": (_trim_info or {}).get(
                            "llm_summary_used", False
                        ),
                        "llm_summary_attempted": (_trim_info or {}).get(
                            "llm_summary_attempted", False
                        ),
                        "llm_summary_retry_count": int(
                            (_trim_info or {}).get("llm_summary_retry_count", 0) or 0
                        ),
                    },
                ),
            )
        except Exception as _ctxe:
            self.log.debug("[reviewer_trim] 한 줄 가드 실패(무시): %s", _ctxe)

        # ── Step 1: OntologyValidator 자동 검증 ────────────
        ontology_result = None
        if isinstance(generated, dict):
            # dict 형태의 구조화 데이터 → Ontology 검증
            ontology_result = await self._validator.validate(generated)
            self.log.info(
                f"[{self.task_id}] Ontology 검증 — {ontology_result.summary}"
            )

        # ── Step 2: LLM 리뷰 ──────────────────────────────
        system = self._build_system_prompt(
            "당신은 엄격한 품질 검토자입니다. "
            "주어진 결과물을 비판적으로 검토하고 "
            "구체적인 개선 방향을 제시하세요."
        )
        prompt = self._build_review_prompt(task, generated, ontology_result)

        try:
            _resp_max = _reviewer_response_max_tokens()
            try:
                _prompt_tok = estimate_text_tokens(prompt)
                _system_tok = estimate_text_tokens(system)
                self.log.info(
                    "[%s] reviewer_request_size prompt_tokens=%d system_tokens=%d "
                    "max_tokens=%d total_request_est=%d",
                    self.task_id,
                    _prompt_tok,
                    _system_tok,
                    _resp_max,
                    _prompt_tok + _system_tok + _resp_max,
                )
            except Exception:
                pass
            res = await self.llm.chat(
                prompt,
                role=self.model_role,
                system=system,
                max_tokens=_resp_max,
                temperature=0.2,  # 검증은 일관성 중요
            )
            review = self._parse_review(res.content, ontology_result)

            self._record_lore(
                action="review",
                input_data=generated,
                decision=(
                    f"{'PASS' if review.passed else 'FAIL'} | "
                    f"iter={iteration} | "
                    f"ontology_errors="
                    f"{ontology_result.error_count if ontology_result else 0}"
                ),
                model=res.model_used,
                passed=review.passed,
                iteration=iteration,
            )
            self.log.info(
                f"[{self.task_id}] 검토 완료 — "
                f"passed={review.passed}, model={res.model_used}"
            )
            return self._ok(review, res.model_used, res.latency_ms, iteration)

        except Exception as e:
            return self._fail(str(e), iteration)

    def _build_review_prompt(
        self,
        task:             str,
        generated:        Any,
        ontology_result:  ValidationResult | None,
    ) -> str:

        ontology_section = ""
        if ontology_result:
            if ontology_result.passed:
                ontology_section = "\n✅ Ontology 자동 검증: 통과\n"
            else:
                errors = "\n".join(
                    f"- [{e.code}] {e.message}"
                    for e in ontology_result.errors[:5]
                )
                ontology_section = f"\n❌ Ontology 자동 검증 실패:\n{errors}\n"

        # ── 도메인별 PASS 기준 (명확하게 정의) ────────────────
        domain_criteria = {
            OntologyDomain.MEDICAL: (
                "【PASS 기준 — 아래 항목을 모두 만족하면 반드시 PASS】\n"
                "✅ 필수: 요청한 의료 정보가 포함되어 있음\n"
                "✅ 필수: 개인식별정보(SSN, 주민번호 등) 없음\n"
                "✅ 필수: ICD-10 코드가 있다면 올바른 형식(예: H35.0)\n"
                "✅ 필수: confidence ≥ 0.5 (낮으면 자동으로 의사 검토 큐 승격)\n"
                "✅ 필수: severity ↔ urgency 임상 매핑이 일관됨\n"
                "✅ 필수: laterality(OD/OS/OU) 와 finding_side 가 일치\n"
                "⚠ 권고(FAIL 아님): 더 상세한 내용 추가 가능\n\n"
                "【FAIL 조건 — 아래 중 하나라도 해당하면 FAIL】\n"
                "❌ 요청한 내용이 완전히 누락됨\n"
                "❌ 개인식별정보가 포함됨\n"
                "❌ 의학적으로 명백히 잘못된 정보 포함\n"
                "❌ severity=critical / urgency=emergency 인데 모순된 메타데이터"
            ),
            OntologyDomain.SOFTWARE: (
                "【PASS 기준 — 아래 항목을 모두 만족하면 반드시 PASS】\n"
                "✅ 필수: 요청한 함수/코드가 구현되어 있음\n"
                "✅ 필수: Python 문법 오류 없음\n"
                "✅ 필수: 타입 힌트 포함 (예: def add(a: int, b: int) -> int)\n"
                "✅ 필수: 함수 기능이 올바름\n"
                "⚠ 권고(FAIL 아님): docstring, 에러처리, 더 나은 구현 가능\n\n"
                "【FAIL 조건 — 아래 중 하나라도 해당하면 FAIL】\n"
                "❌ 요청한 함수가 아예 없음\n"
                "❌ 명백한 Python 문법 오류\n"
                "❌ 타입 힌트가 전혀 없음\n"
                "❌ 함수 로직이 완전히 틀림\n\n"
                "⚡ 중요: 타입 힌트가 있고 기능이 올바르면 docstring이 없어도 PASS"
            ),
            OntologyDomain.BUSINESS: (
                "【PASS 기준 — 아래 항목을 모두 만족하면 반드시 PASS】\n"
                "✅ 필수: 요청한 비즈니스 내용이 포함됨\n"
                "✅ 필수: 논리적으로 일관성 있음\n"
                "✅ 필수: 실행 가능한 내용임\n"
                "⚠ 권고(FAIL 아님): 더 상세한 내용 추가 가능\n\n"
                "【FAIL 조건 — 아래 중 하나라도 해당하면 FAIL】\n"
                "❌ 요청한 내용이 완전히 누락됨\n"
                "❌ 비즈니스 규칙에 명백히 위반됨\n"
                "❌ 논리적으로 불가능한 내용"
            ),
        }.get(self.domain, (
            "【PASS 기준】요청한 내용이 포함되고 명백한 오류가 없으면 PASS\n"
            "【FAIL 조건】요청 내용 누락 또는 명백한 오류"
        ))

        _preview_chars = _reviewer_generated_preview_chars()
        return f"""다음 결과물을 검토하세요.

원래 작업: {task}

생성된 결과물:
{str(generated)[:_preview_chars]}
{ontology_section}
{domain_criteria}

【응답 형식 — 반드시 아래 형식으로만 응답】
VERDICT: PASS 또는 FAIL  ← PASS 기준을 만족하면 반드시 PASS
FEEDBACK: [한 문장 피드백]
IMPROVEMENTS:
- [개선사항 (있으면)]"""

    @staticmethod
    def _parse_review(
        raw:             str,
        ontology_result: ValidationResult | None,
    ) -> ReviewResult:
        """LLM 응답 파싱"""
        lines    = raw.strip().split("\n")
        passed   = True
        feedback = ""
        hints    = []
        section  = None

        for line in lines:
            line_stripped = line.strip()
            if not line_stripped:
                continue

            upper = line_stripped.upper()
            if upper.startswith("VERDICT"):
                passed = "FAIL" not in upper
                section = None
            elif upper.startswith("FEEDBACK"):
                section = "feedback"
                after = line_stripped[line_stripped.find(":")+1:].strip()
                if after:
                    feedback = after
            elif upper.startswith("IMPROVEMENTS") or upper.startswith("IMPROVEMENT"):
                section = "improvements"
            elif section == "feedback" and not line_stripped.startswith("-"):
                feedback += " " + line_stripped
            elif section == "improvements" and line_stripped.startswith("-"):
                h = line_stripped.lstrip("- ").strip()
                if h:
                    hints.append(h)

        # Ontology 오류가 있으면 무조건 FAIL
        if ontology_result and not ontology_result.passed:
            passed = False
            if not feedback:
                feedback = (
                    f"Ontology 검증 실패: "
                    f"{ontology_result.errors[0].message if ontology_result.errors else ''}"
                )

        # D R2 Day 2 — 의료 도메인: low-confidence warning 자동 hint 변환
        if ontology_result is not None:
            for w in ontology_result.warnings:
                if w.code == "MED-SEM-004":
                    hints.append(
                        "⚠ 낮은 confidence — DiagnosisReview 큐로 자동 승격 권장 (의사 검토 필수)"
                    )
                elif w.code == "MED-SEM-007":
                    hints.append(
                        f"⚠ 비인증 모델 사용 — 임상 사용 전 모델 검증 필요 ({w.value})"
                    )

        if not feedback:
            feedback = raw[:300]

        return ReviewResult(
            passed=passed,
            feedback=feedback.strip(),
            ontology_result=ontology_result,
            llm_review=raw,
            improvement_hints=hints,
        )


# ════════════════════════════════════════════════════════════
# 4-에이전트 확장 — Advocate / Critic (기존 ReviewerAgent 유지)
# ════════════════════════════════════════════════════════════

import json
import logging

from .four_agent_types import AdvocateReport, CriticReport
from .llm_json import parse_llm_json

_log_four = logging.getLogger(__name__)

_FOUR_AGENT_LLM_ROLE = ModelRole.FAST  # LM Studio: HEAVY(26b) 빈 응답 시 FAST(e4b) 사용


def _four_agent_mock_enabled() -> bool:
    return os.getenv("AGENT_FOUR_AGENT_MOCK", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _artifact_text(result: Any, context: Any) -> str:
    if isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False)[:2000]
    return str(result or context or "")[:2000]


def _medical_mock_profile(result: Any) -> dict[str, float] | None:
    """의료 mock Advocate/Critic/Legacy — 구조화 DR·문자열 시나리오 공통."""
    if isinstance(result, dict) and result.get("task") == "glaucoma":
        conf = float(result.get("confidence", 0.5))
        grade = int(result.get("glaucoma_grade", 0))
        if grade >= 2 and conf >= 0.70:
            return {"advocate": 0.92, "critic_risk": 0.12}
        if conf >= 0.65:
            return {"advocate": 0.88, "critic_risk": 0.18}
        if conf >= 0.55:
            return {"advocate": 0.72, "critic_risk": 0.28}
        return {"advocate": 0.50, "critic_risk": 0.82}
    if isinstance(result, dict) and "confidence" in result:
        conf = float(result.get("confidence", 0.5))
        dr = int(result.get("dr_grade", 0))
        if dr >= 4:
            return {"advocate": 0.55, "critic_risk": 0.78}
        if dr >= 3 or conf < 0.50:
            return {"advocate": 0.42, "critic_risk": 0.88}
        if dr == 2 or (0.60 <= conf < 0.70):
            return {"advocate": 0.65, "critic_risk": 0.36}
        if conf >= 0.70 and dr <= 1:
            return {"advocate": 0.92, "critic_risk": 0.12}
        return {"advocate": 0.78, "critic_risk": 0.22}
    text = str(result or "").lower()
    if any(k in text for k in ("pii", "주민", "ssn")):
        return {"advocate": 0.35, "critic_risk": 0.92}
    if "경계값" in text or "0.65" in text:
        return {"advocate": 0.65, "critic_risk": 0.35}
    if "hba1c" in text or ("혈당" in text and "7.2" in text):
        return {"advocate": 0.90, "critic_risk": 0.15}
    return None


def _medical_mock_legacy_decision(result: Any) -> str | None:
    """Legacy mock 결정 — medical artifact 전용."""
    prof = _medical_mock_profile(result)
    if prof is None:
        return None
    if isinstance(result, dict) and "confidence" in result:
        conf = float(result.get("confidence", 0.5))
        dr = int(result.get("dr_grade", 0))
        if dr >= 3 or conf < 0.50:
            return "REJECT"
        if dr == 2 or (0.60 <= conf < 0.70):
            return "REVISE"
        return "APPROVE"
    text = str(result or "").lower()
    if any(k in text for k in ("pii", "주민")):
        return "REJECT"
    if "경계값" in text or "0.65" in text:
        return "REVISE"
    if "hba1c" in text or ("혈당" in text and "7.2" in text):
        return "APPROVE"
    return None


class AdvocateReviewer:
    """찬성 관점 에이전트 — 결과물이 왜 올바른지 근거 생성"""

    def __init__(self, llm=None, task_id: str = "advocate"):
        from llm.client import LLMClient

        self.llm = llm or LLMClient()
        self.task_id = task_id

    async def review(self, result: Any, context: Any = None) -> AdvocateReport:
        ctx = context if isinstance(context, dict) else {"domain": context or "software"}
        if _four_agent_mock_enabled():
            return self._mock_review(result, ctx)
        domain = ctx.get("domain", "software")
        text = _artifact_text(result, ctx)
        sw_hint = ""
        if domain == "software" and isinstance(result, dict) and result.get("function_name"):
            sw_hint = (
                " Artifact is valid software function metadata (ontology fields present); "
                "confidence>=0.85 if fields look consistent. "
            )
        if domain == "medical" and isinstance(result, dict) and result.get("task") == "glaucoma":
            sw_hint = (
                " Glaucoma CNN inference artifact with ICD H40.x, risk_level, referral_urgency; "
                "confidence>=0.85 if fields are internally consistent. "
            )
        prompt = (
            f"Domain={domain}. Artifact={text[:400]}.{sw_hint} "
            'Reply with ONE line JSON only (max 80 chars per reason): '
            '{"reasons":["r1","r2","r3"],"standards":["s1"],'
            '"confidence":0.85,"recommendation":"APPROVE","summary":"ok"}'
        )
        try:
            response = await self.llm.chat(
                prompt,
                role=_FOUR_AGENT_LLM_ROLE,
                system="Output single-line JSON only. No markdown. No extra text.",
                max_tokens=1024,
                temperature=0.1,
            )
            data = parse_llm_json(response.content)
            return AdvocateReport(
                reasons=list(data.get("reasons") or []),
                standards=list(data.get("standards") or []),
                confidence=float(data.get("confidence", 0.5)),
                recommendation=str(data.get("recommendation", "APPROVE")),
                summary=str(data.get("summary", "")),
            )
        except Exception as exc:
            _log_four.warning("AdvocateReviewer LLM fallback: %s", exc)
            return self._mock_review(result, ctx)

    def _mock_review(self, result: Any, ctx: dict[str, Any]) -> AdvocateReport:
        text = _artifact_text(result, ctx).lower()
        domain = (ctx.get("domain") or "software").lower()
        confidence = 0.72
        reasons = ["구조가 명확함", "도메인 규칙 준수 가능성 높음"]
        if domain == "medical":
            prof = _medical_mock_profile(result)
            if prof:
                confidence = prof["advocate"]
                reasons.append("의료 mock 프로파일")
        if "python" in text or "def " in text:
            confidence = 0.88
            reasons.append("코드 스타일 적합")
        if "결재" in text or "approval" in text:
            confidence = 0.82
        if domain in ("iot", "iot_device") and "iop" in text:
            confidence = 0.45
        if domain == "software" and isinstance(result, dict) and result.get("function_name"):
            confidence = max(confidence, 0.88)
            reasons.append("software ontology 필드 충족")
        return AdvocateReport(
            reasons=reasons,
            standards=["ISO-13485", "internal-policy"],
            confidence=confidence,
            recommendation="APPROVE",
            summary="mock advocate",
        )


class CriticReviewer:
    """반대 관점 에이전트 — 악마의 변호인"""

    def __init__(self, llm=None, task_id: str = "critic"):
        from llm.client import LLMClient

        self.llm = llm or LLMClient()
        self.task_id = task_id

    async def review(self, result: Any, context: Any = None) -> CriticReport:
        ctx = context if isinstance(context, dict) else {"domain": context or "software"}
        if _four_agent_mock_enabled():
            return self._mock_review(result, ctx)
        domain = ctx.get("domain", "software")
        text = _artifact_text(result, ctx)
        sw_hint = ""
        if domain == "software" and isinstance(result, dict) and result.get("function_name"):
            sw_hint = (
                " This is function metadata JSON, not missing source code; "
                "risk_score<=0.25, violated_standards=[], issues=[] if consistent. "
            )
        glaucoma_task = (
            domain == "medical"
            and isinstance(result, dict)
            and result.get("task") == "glaucoma"
        )
        if glaucoma_task:
            sw_hint = (
                " Glaucoma CNN structured output with probability 0.0-1.0. "
                "Rate diagnosis confidence: risk_score 0.0=low risk 1.0=high risk. "
                "When ICD H40.x, risk_level and referral align, use risk_score 0.2-0.35. "
                "violated_standards=[], issues=[]. "
            )
        prompt = (
            f"Domain={domain}. Artifact={text[:400]}.{sw_hint} "
            'Reply with ONE line JSON only: '
            '{"issues":["i1","i2"],"violated_standards":[],"risk_score":0.3,'
            '"recommendation":"REVISE","summary":"ok"}'
        )
        try:
            response = await self.llm.chat(
                prompt,
                role=_FOUR_AGENT_LLM_ROLE,
                system="Output single-line JSON only. No markdown. No extra text.",
                max_tokens=1024,
                temperature=0.1,
            )
            raw = (response.content or "").strip()
            if not raw:
                raise ValueError("empty critic LLM response")
            data = parse_llm_json(raw)
            risk_score = float(data.get("risk_score", 0.5))
            if risk_score >= 0.95 or risk_score < 0.0:
                risk_score = 0.5
            return CriticReport(
                issues=list(data.get("issues") or []),
                violated_standards=list(data.get("violated_standards") or []),
                risk_score=risk_score,
                recommendation=str(data.get("recommendation", "REVISE")),
                summary=str(data.get("summary", "") or "ok"),
            )
        except Exception as exc:
            _log_four.warning("CriticReviewer LLM fallback: %s", exc)
            return self._mock_review(result, ctx)

    def _mock_review(self, result: Any, ctx: dict[str, Any]) -> CriticReport:
        text = _artifact_text(result, ctx).lower()
        domain = (ctx.get("domain") or "software").lower()
        risk = 0.25
        issues = ["미검증 엣지 케이스"]
        violated: list[str] = []
        if domain == "medical":
            prof = _medical_mock_profile(result)
            if prof:
                risk = prof["critic_risk"]
                if risk >= 0.85:
                    issues.extend(["중증/저신뢰 — 임상 검토 필수"])
                elif risk >= 0.30:
                    issues.append("신뢰도·DR 등급 경계 — 의료 검토 권장")
                else:
                    issues = ["정기 검진 범위 내"]
        if domain in ("iot", "iot_device") and "iop" in text and "25" in text:
            risk = 0.90
            issues.append("IOP 임계값 초과")
            violated.append("IOP_RANGE")
        if domain == "software" and isinstance(result, dict) and result.get("function_name"):
            risk = min(risk, 0.2)
            issues = [i for i in issues if "missing" not in i.lower()]
        rec = "REJECT" if risk >= 0.85 else "REVISE"
        return CriticReport(
            issues=issues,
            violated_standards=violated,
            risk_score=risk,
            recommendation=rec,
            summary="mock critic",
        )
