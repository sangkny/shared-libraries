# shared-libraries/agents/generator.py
"""
GeneratorAgent — 실제 결과물 생성
- 모델: FAST (gemma-4-e4b) — 빠른 반복
- 역할: PlannerAgent의 계획을 받아 실제 결과물 생성
- 출력: 코드 / 의료 보고서 / 비즈니스 문서 등
"""
from llm.base import ModelRole
from ontology.base import OntologyDomain
from .base import BaseAgent, AgentType, AgentResult
from .planner import ExecutionPlan


class GeneratorAgent(BaseAgent):
    """
    계획을 받아 실제 결과물을 생성하는 Agent

    사용법:
        generator = GeneratorAgent(domain=OntologyDomain.SOFTWARE)
        result    = await generator.run(
            "calculate_bmi 함수 구현",
            context={"plan": execution_plan}
        )
        code = result.output
    """

    @property
    def agent_type(self) -> AgentType:
        return AgentType.GENERATOR

    @property
    def model_role(self) -> ModelRole:
        return ModelRole.FAST

    async def run(
        self,
        task:    str,
        context: dict | None = None,
    ) -> AgentResult:
        self.log.info(f"[{self.task_id}] GeneratorAgent 시작")
        ctx       = context or {}
        iteration = ctx.get("iteration", 0)
        plan      = ctx.get("plan")         # ExecutionPlan (있으면 활용)
        feedback  = ctx.get("feedback", "") # ReviewerAgent 피드백 (재생성 시)

        system = self._build_system_prompt(
            "주어진 작업에 대해 고품질의 결과물을 생성하세요. "
            "명확하고 완성도 높은 출력을 제공하세요."
        )
        prompt = self._build_prompt(task, plan, feedback, ctx)

        try:
            res = await self.llm.chat(
                prompt,
                role=self.model_role,
                system=system,
                max_tokens=2048,
                temperature=0.7,
            )
            self._record_lore(
                action="generate",
                input_data=task,
                decision=f"생성 완료 | iter={iteration} | chars={len(res.content)}",
                model=res.model_used,
                passed=True,
                iteration=iteration,
            )
            self.log.info(
                f"[{self.task_id}] 생성 완료 — "
                f"chars={len(res.content)}, model={res.model_used}"
            )
            return self._ok(res.content, res.model_used, res.latency_ms, iteration)

        except Exception as e:
            return self._fail(str(e), iteration)

    def _build_prompt(
        self,
        task:     str,
        plan:     ExecutionPlan | None,
        feedback: str,
        context:  dict,
    ) -> str:

        # 계획이 있으면 계획 기반으로 생성
        plan_section = ""
        if plan:
            plan_section = f"\n실행 계획:\n{plan.to_prompt()}\n"

        # 피드백이 있으면 (재생성) 피드백 반영
        feedback_section = ""
        if feedback:
            feedback_section = (
                f"\n⚠ 이전 결과에 대한 수정 요청:\n{feedback}\n"
                f"위 피드백을 반드시 반영하여 개선된 결과물을 생성하세요.\n"
            )

        # 도메인별 출력 형식 가이드
        output_guide = {
            OntologyDomain.MEDICAL: (
                "출력 형식: 구조화된 의료 보고서\n"
                "- 환자 정보 요약\n"
                "- 진단 소견\n"
                "- 권장 치료 계획\n"
                "주의: 개인식별정보(PII) 포함 금지"
            ),
            OntologyDomain.SOFTWARE: (
                "출력 형식: Python 코드\n"
                "- 타입 힌트 필수\n"
                "- docstring 포함\n"
                "- 에러 처리 포함\n"
                "- 50줄 이내 함수"
            ),
            OntologyDomain.BUSINESS: (
                "출력 형식: 비즈니스 문서\n"
                "- 명확한 섹션 구분\n"
                "- 실행 가능한 액션 아이템\n"
                "- 책임자 및 기한 명시"
            ),
        }.get(self.domain, "명확하고 완성도 높은 결과물을 생성하세요.")

        return f"""다음 작업의 결과물을 생성하세요.

작업: {task}
도메인: {self.domain.value}
{plan_section}{feedback_section}
{output_guide}"""
