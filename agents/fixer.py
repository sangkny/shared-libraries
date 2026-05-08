# shared-libraries/agents/fixer.py
"""
FixerAgent — 자동 수정
- 모델: FAST (gemma-4-e4b) — 빠른 반복
- 역할: ReviewerAgent 피드백을 받아 결과물 수정
- 출력: 수정된 결과물
"""
from llm.base import ModelRole
from ontology.base import OntologyDomain
from .base import BaseAgent, AgentType, AgentResult
from .reviewer import ReviewResult


class FixerAgent(BaseAgent):
    """
    ReviewerAgent 피드백 기반 자동 수정 Agent

    사용법:
        fixer  = FixerAgent(domain=OntologyDomain.SOFTWARE)
        result = await fixer.run(
            "원래 작업",
            context={
                "generated": 이전_생성_결과,
                "review":    review_result,
                "iteration": 1,
            }
        )
        fixed = result.output
    """

    @property
    def agent_type(self) -> AgentType:
        return AgentType.FIXER

    @property
    def model_role(self) -> ModelRole:
        return ModelRole.FAST

    async def run(
        self,
        task:    str,
        context: dict | None = None,
    ) -> AgentResult:
        self.log.info(f"[{self.task_id}] FixerAgent 시작")
        ctx       = context or {}
        iteration = ctx.get("iteration", 0)
        generated = ctx.get("generated", "")   # 수정할 결과물
        review    = ctx.get("review")           # ReviewResult

        if not generated:
            return self._fail("수정할 결과물이 없습니다.", iteration)

        system = self._build_system_prompt(
            "당신은 코드/문서 수정 전문가입니다. "
            "피드백을 정확히 반영하여 결과물을 개선하세요. "
            "원래 내용의 좋은 부분은 유지하고 문제점만 수정하세요."
        )
        prompt = self._build_fix_prompt(task, generated, review)

        try:
            res = await self.llm.chat(
                prompt,
                role=self.model_role,
                system=system,
                max_tokens=2048,
                temperature=0.5,
            )
            self._record_lore(
                action="fix",
                input_data=generated,
                decision=(
                    f"수정 완료 | iter={iteration} | "
                    f"errors_fixed="
                    f"{len(review.ontology_result.errors) if review and review.ontology_result else 0}"
                ),
                model=res.model_used,
                passed=True,
                iteration=iteration,
            )
            self.log.info(
                f"[{self.task_id}] 수정 완료 — "
                f"iter={iteration}, model={res.model_used}"
            )
            return self._ok(res.content, res.model_used, res.latency_ms, iteration)

        except Exception as e:
            return self._fail(str(e), iteration)

    def _build_fix_prompt(
        self,
        task:      str,
        generated: str,
        review:    ReviewResult | None,
    ) -> str:

        # 수정 요청 내용 구성
        fix_request = ""
        if review:
            fix_request = review.to_fixer_prompt()
        else:
            fix_request = "품질을 개선하고 누락된 내용을 보완하세요."

        # 도메인별 수정 가이드
        fix_guide = {
            OntologyDomain.MEDICAL: (
                "수정 시 주의사항:\n"
                "- ICD-10 코드는 표준 형식(예: H35.0) 유지\n"
                "- 개인식별정보 제거 또는 마스킹\n"
                "- 의료 용어 표준화"
            ),
            OntologyDomain.SOFTWARE: (
                "수정 시 주의사항:\n"
                "- 기존 함수 시그니처 유지 (가능한 경우)\n"
                "- 타입 힌트 추가/수정\n"
                "- 에러 처리 보완"
            ),
            OntologyDomain.BUSINESS: (
                "수정 시 주의사항:\n"
                "- 비즈니스 규칙 일관성 유지\n"
                "- 책임자/기한 명시\n"
                "- 승인 프로세스 완성"
            ),
        }.get(self.domain, "")

        return f"""다음 결과물을 수정하세요.

원래 작업: {task}

현재 결과물:
{str(generated)[:1500]}

수정 요청:
{fix_request}

{fix_guide}

수정된 완성 결과물만 출력하세요. 설명이나 코멘트 없이 결과물만 제공하세요."""
