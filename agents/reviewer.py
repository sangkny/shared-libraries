# shared-libraries/agents/reviewer.py
"""
ReviewerAgent — Ontology 기반 검증
- 모델: HEAVY (gemma-4-26b-a4b) — 고품질 추론
- 역할: GeneratorAgent 결과물을 OntologyValidator로 검증 + LLM 리뷰
- 출력: ReviewResult (passed/failed + 피드백)
"""
from dataclasses import dataclass, field
from llm.base import ModelRole
from ontology.base import OntologyDomain, ValidationResult
from ontology.validator import OntologyValidator
from .base import BaseAgent, AgentType, AgentResult


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

    def __init__(self, *args, ontology_rules: dict | None = None, **kwargs):
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
        context: dict | None = None,
    ) -> AgentResult:
        self.log.info(f"[{self.task_id}] ReviewerAgent 시작 (HEAVY 모델)")
        ctx       = context or {}
        iteration = ctx.get("iteration", 0)
        generated = ctx.get("generated", task)  # 검증할 결과물

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
            res = await self.llm.chat(
                prompt,
                role=self.model_role,
                system=system,
                max_tokens=1024,
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
        generated:        any,
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
                "⚠ 권고(FAIL 아님): 더 상세한 내용 추가 가능\n\n"
                "【FAIL 조건 — 아래 중 하나라도 해당하면 FAIL】\n"
                "❌ 요청한 내용이 완전히 누락됨\n"
                "❌ 개인식별정보가 포함됨\n"
                "❌ 의학적으로 명백히 잘못된 정보 포함"
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

        return f"""다음 결과물을 검토하세요.

원래 작업: {task}

생성된 결과물:
{str(generated)[:1500]}
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

        if not feedback:
            feedback = raw[:300]

        return ReviewResult(
            passed=passed,
            feedback=feedback.strip(),
            ontology_result=ontology_result,
            llm_review=raw,
            improvement_hints=hints,
        )
