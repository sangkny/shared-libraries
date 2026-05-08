# shared-libraries/agents/planner.py
"""
PlannerAgent — 작업 계획 수립
- 모델: FAST (gemma-4-e4b) — 빠른 반복
- 역할: 복잡한 작업을 단계별 실행 계획으로 분해
- 출력: 구조화된 실행 계획 (단계 목록)
"""
from dataclasses import dataclass, field
from llm.base import ModelRole
from ontology.base import OntologyDomain
from .base import BaseAgent, AgentType, AgentResult


@dataclass
class ExecutionPlan:
    """PlannerAgent 출력 — 구조화된 실행 계획"""
    goal:        str
    steps:       list[str]
    domain:      str
    constraints: list[str] = field(default_factory=list)
    estimated_iterations: int = 1

    def to_prompt(self) -> str:
        """GeneratorAgent에 전달할 프롬프트 형태로 변환"""
        steps_str = "\n".join(f"{i+1}. {s}" for i, s in enumerate(self.steps))
        constraints_str = "\n".join(f"- {c}" for c in self.constraints)
        return (
            f"목표: {self.goal}\n\n"
            f"실행 단계:\n{steps_str}\n\n"
            f"제약조건:\n{constraints_str if self.constraints else '없음'}"
        )


class PlannerAgent(BaseAgent):
    """
    작업을 단계별 실행 계획으로 분해하는 Agent

    사용법:
        planner = PlannerAgent(domain=OntologyDomain.MEDICAL)
        result  = await planner.run("환자 안저 이미지 분석 보고서 생성")
        plan    = result.output  # ExecutionPlan
    """

    @property
    def agent_type(self) -> AgentType:
        return AgentType.PLANNER

    @property
    def model_role(self) -> ModelRole:
        return ModelRole.FAST

    async def run(
        self,
        task:    str,
        context: dict | None = None,
    ) -> AgentResult:
        self.log.info(f"[{self.task_id}] PlannerAgent 시작 — task={task[:80]}")
        ctx = context or {}

        system = self._build_system_prompt(
            "주어진 작업을 명확하고 실행 가능한 단계로 분해하세요. "
            "각 단계는 구체적이고 독립적으로 실행 가능해야 합니다."
        )

        prompt = self._build_prompt(task, ctx)

        try:
            res = await self.llm.chat(
                prompt,
                role=self.model_role,
                system=system,
                max_tokens=1024,
                temperature=0.3,  # 계획은 일관성 중요 — 낮은 temperature
            )
            plan = self._parse_plan(task, res.content)
            self._record_lore(
                action="plan",
                input_data=task,
                decision=f"steps={len(plan.steps)}: {plan.steps[0] if plan.steps else ''}",
                model=res.model_used,
                passed=True,
                iteration=0,
            )
            self.log.info(
                f"[{self.task_id}] 계획 완료 — "
                f"{len(plan.steps)}단계, model={res.model_used}"
            )
            return self._ok(plan, res.model_used, res.latency_ms)

        except Exception as e:
            return self._fail(str(e))

    def _build_prompt(self, task: str, context: dict) -> str:
        domain_hint = {
            OntologyDomain.MEDICAL:  "의료 데이터 처리 규정과 환자 개인정보 보호를 고려하세요.",
            OntologyDomain.SOFTWARE: "코드 품질, 테스트 가능성, 유지보수성을 고려하세요.",
            OntologyDomain.BUSINESS: "비즈니스 규칙, 승인 흐름, 감사 추적을 고려하세요.",
        }.get(self.domain, "")

        prev_context = ""
        if context.get("previous_result"):
            prev_context = f"\n이전 결과: {str(context['previous_result'])[:300]}"

        return f"""다음 작업에 대한 실행 계획을 수립하세요.

작업: {task}
도메인: {self.domain.value}
{domain_hint}
{prev_context}

다음 형식으로 응답하세요:
STEPS:
1. [첫 번째 단계]
2. [두 번째 단계]
3. [세 번째 단계]
...

CONSTRAINTS:
- [제약조건 1]
- [제약조건 2]

ITERATIONS: [예상 반복 횟수, 숫자만]"""

    @staticmethod
    def _parse_plan(task: str, raw: str) -> ExecutionPlan:
        """LLM 응답을 ExecutionPlan으로 파싱"""
        steps, constraints, iterations = [], [], 1

        lines = raw.strip().split("\n")
        section = None

        for line in lines:
            line = line.strip()
            if not line:
                continue

            if line.upper().startswith("STEPS"):
                section = "steps"
            elif line.upper().startswith("CONSTRAINTS"):
                section = "constraints"
            elif line.upper().startswith("ITERATIONS"):
                section = "iterations"
                try:
                    iterations = int(''.join(filter(str.isdigit, line)))
                except ValueError:
                    iterations = 1
            elif section == "steps" and (
                line[0].isdigit() or line.startswith("-")
            ):
                step = line.lstrip("0123456789.-) ").strip()
                if step:
                    steps.append(step)
            elif section == "constraints" and line.startswith("-"):
                c = line.lstrip("- ").strip()
                if c:
                    constraints.append(c)

        # 파싱 실패 시 전체 응답을 단일 단계로
        if not steps:
            steps = [raw.strip()[:200]]

        return ExecutionPlan(
            goal=task,
            steps=steps,
            domain="general",
            constraints=constraints,
            estimated_iterations=max(1, min(iterations, 5)),
        )
