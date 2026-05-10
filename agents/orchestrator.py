# shared-libraries/agents/orchestrator.py
"""
Orchestrator — 4가지 autopus-ADK 전략 기반 워크플로우
- PIPELINE:  Planner → Generator → Reviewer → Fixer (기본)
- CONSENSUS: E4B + 26B 동시 검증, 둘 다 통과해야 승인
- DEBATE:    두 모델이 논쟁 → Orchestrator가 최선 선택
- FASTEST:   타임아웃 시 즉시 응답 (fallback)
"""
import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from llm.base import ModelRole
from ontology.base import OntologyDomain
from .base import BaseAgent, AgentType, AgentResult, AgentStatus, LoreEntry
from .planner import PlannerAgent
from .generator import GeneratorAgent
from .reviewer import ReviewerAgent
from .fixer import FixerAgent


# ── 전략 정의 ─────────────────────────────────────────────
class OrchestraStrategy(Enum):
    PIPELINE  = "pipeline"   # 순서대로 통과
    CONSENSUS = "consensus"  # 두 모델 모두 통과
    DEBATE    = "debate"     # 두 모델 논쟁 후 최선 선택
    FASTEST   = "fastest"    # 타임아웃 fallback


# ── Orchestrator 최종 결과 ────────────────────────────────
@dataclass
class OrchestratorResult:
    """Orchestrator 전체 실행 결과"""
    task_id:    str
    strategy:   OrchestraStrategy
    domain:     OntologyDomain
    passed:     bool
    output:     Any
    iterations: int
    lore:       list[LoreEntry]       = field(default_factory=list)
    agent_results: list[AgentResult]  = field(default_factory=list)
    total_latency_ms: float           = 0.0
    error:      str                   = ""

    @property
    def summary(self) -> str:
        status = "✅ PASS" if self.passed else "❌ FAIL"
        return (
            f"{status} | strategy={self.strategy.value} | "
            f"domain={self.domain.value} | "
            f"iter={self.iterations} | "
            f"latency={self.total_latency_ms:.0f}ms"
        )


class Orchestrator(BaseAgent):
    """
    autopus-ADK orchestra 전략 기반 멀티 Agent 워크플로우

    사용법:
        # MEDI-IOT — CONSENSUS 전략 (의료 안전)
        orch = Orchestrator(
            domain=OntologyDomain.MEDICAL,
            strategy=OrchestraStrategy.CONSENSUS,
            max_iterations=3,
        )
        result = await orch.run("환자 안저 이미지 분석 보고서 생성")
        print(result.summary)

        # AutoNoGaDa — PIPELINE 전략 (빠른 코드 생성)
        orch = Orchestrator(
            domain=OntologyDomain.SOFTWARE,
            strategy=OrchestraStrategy.PIPELINE,
        )

        # CoOps — DEBATE 전략 (최선의 비즈니스 판단)
        orch = Orchestrator(
            domain=OntologyDomain.BUSINESS,
            strategy=OrchestraStrategy.DEBATE,
        )
    """

    MAX_ITERATIONS = 3
    TIMEOUT_SECONDS = 30.0

    def __init__(
        self,
        domain:         OntologyDomain = OntologyDomain.GENERAL,
        strategy:       OrchestraStrategy = OrchestraStrategy.PIPELINE,
        max_iterations: int = 3,
        fastest_timeout_sec: float | None = None,
        **kwargs,
    ):
        super().__init__(domain=domain, **kwargs)
        self.strategy       = strategy
        self.max_iterations = min(max_iterations, self.MAX_ITERATIONS)
        self._fastest_timeout = (
            float(fastest_timeout_sec)
            if fastest_timeout_sec is not None
            else self.TIMEOUT_SECONDS
        )

        # Agent 초기화 (동일 task_id + llm 공유)
        agent_kwargs = dict(domain=domain, llm=self.llm, task_id=self.task_id)
        self._planner   = PlannerAgent(**agent_kwargs)
        self._generator = GeneratorAgent(**agent_kwargs)
        self._reviewer  = ReviewerAgent(**agent_kwargs)
        self._fixer     = FixerAgent(**agent_kwargs)

    @property
    def agent_type(self) -> AgentType:
        return AgentType.ORCHESTRATOR

    @property
    def model_role(self) -> ModelRole:
        return ModelRole.FAST

    # ── 메인 실행 ─────────────────────────────────────────

    async def run(
        self,
        task:    str,
        context: dict | None = None,
    ) -> AgentResult:
        """AgentResult 래퍼 — OrchestratorResult를 output에 담아 반환"""
        result = await self.execute(task, context)
        if result.passed:
            return self._ok(result, latency=result.total_latency_ms)
        return self._fail(result.error or "검증 실패", result.iterations)

    async def execute(
        self,
        task:    str,
        context: dict | None = None,
    ) -> OrchestratorResult:
        """전략에 따라 실행"""
        self.log.info(
            f"[{self.task_id}] Orchestrator 시작 — "
            f"strategy={self.strategy.value} | domain={self.domain.value}"
        )
        import time
        t0 = time.monotonic()

        try:
            if self.strategy == OrchestraStrategy.PIPELINE:
                result = await self._run_pipeline(task, context or {})
            elif self.strategy == OrchestraStrategy.CONSENSUS:
                result = await self._run_consensus(task, context or {})
            elif self.strategy == OrchestraStrategy.DEBATE:
                result = await self._run_debate(task, context or {})
            else:  # FASTEST
                result = await self._run_fastest(task, context or {})
        except Exception as e:
            self.log.error(f"[{self.task_id}] Orchestrator 오류: {e}")
            result = OrchestratorResult(
                task_id=self.task_id,
                strategy=self.strategy,
                domain=self.domain,
                passed=False,
                output=None,
                iterations=0,
                error=str(e),
            )

        result.total_latency_ms = (time.monotonic() - t0) * 1000
        self.log.info(f"[{self.task_id}] {result.summary}")
        return result

    # ── 전략 1: PIPELINE ──────────────────────────────────
    async def _run_pipeline(
        self, task: str, context: dict
    ) -> OrchestratorResult:
        """
        Planner → Generator → Reviewer → Fixer (최대 3회 반복)
        기본 전략 — AutoNoGaDa 코드 생성에 적합
        """
        agent_results = []

        # Step 1: Plan
        plan_result = await self._planner.run(task, context)
        agent_results.append(plan_result)
        plan = plan_result.output if plan_result.success else None

        generated = None
        for i in range(self.max_iterations):
            self.log.info(f"[{self.task_id}] Pipeline iter={i+1}")

            # Step 2: Generate
            gen_ctx = {"plan": plan, "iteration": i,
                       "feedback": context.get("feedback", "")}
            gen_result = await self._generator.run(task, gen_ctx)
            agent_results.append(gen_result)

            if not gen_result.success:
                return OrchestratorResult(
                    task_id=self.task_id, strategy=self.strategy,
                    domain=self.domain, passed=False, output=None,
                    iterations=i+1, agent_results=agent_results,
                    lore=self._collect_lore(),
                    error="GeneratorAgent 실패",
                )
            generated = gen_result.output

            # Step 3: Review
            rev_ctx = {"generated": generated, "iteration": i}
            rev_result = await self._reviewer.run(task, rev_ctx)
            agent_results.append(rev_result)

            review = rev_result.output
            if rev_result.success and review and review.passed:
                # ✅ 통과!
                return OrchestratorResult(
                    task_id=self.task_id, strategy=self.strategy,
                    domain=self.domain, passed=True, output=generated,
                    iterations=i+1, agent_results=agent_results,
                    lore=self._collect_lore(),
                )

            # Step 4: Fix (마지막 iter가 아니면)
            if i < self.max_iterations - 1:
                fix_ctx = {
                    "generated": generated,
                    "review":    review,
                    "iteration": i,
                }
                fix_result = await self._fixer.run(task, fix_ctx)
                agent_results.append(fix_result)
                if fix_result.success:
                    generated = fix_result.output
                    context["feedback"] = review.feedback if review else ""

        # 최대 반복 초과 — 마지막 결과 반환
        return OrchestratorResult(
            task_id=self.task_id, strategy=self.strategy,
            domain=self.domain, passed=False, output=generated,
            iterations=self.max_iterations, agent_results=agent_results,
            lore=self._collect_lore(),
            error=f"{self.max_iterations}회 반복 후 검증 통과 실패",
        )

    # ── 전략 2: CONSENSUS ─────────────────────────────────
    async def _run_consensus(
        self, task: str, context: dict
    ) -> OrchestratorResult:
        """
        FAST + HEAVY 모델 동시 검증 — 둘 다 PASS해야 승인
        MEDI-IOT 의료 데이터 검증에 적합 (안전 최우선)
        """
        agent_results = []

        # Plan
        plan_result = await self._planner.run(task, context)
        agent_results.append(plan_result)
        plan = plan_result.output if plan_result.success else None

        for i in range(self.max_iterations):
            self.log.info(f"[{self.task_id}] Consensus iter={i+1}")

            # Generate
            gen_result = await self._generator.run(
                task, {"plan": plan, "iteration": i}
            )
            agent_results.append(gen_result)
            if not gen_result.success:
                break
            generated = gen_result.output

            # FAST + HEAVY 동시 검증
            fast_reviewer = ReviewerAgent(
                domain=self.domain, llm=self.llm, task_id=self.task_id
            )
            heavy_reviewer = ReviewerAgent(
                domain=self.domain, llm=self.llm, task_id=self.task_id
            )

            # FAST는 기본 역할, HEAVY는 강제로 HEAVY 모델 사용
            rev_ctx = {"generated": generated, "iteration": i}
            fast_task  = asyncio.create_task(fast_reviewer.run(task, rev_ctx))

            # HEAVY 모델로 별도 리뷰
            heavy_prompt = (
                f"[HEAVY 검증] 다음 결과물을 상세히 검토하세요:\n\n"
                f"작업: {task}\n결과물: {str(generated)[:1000]}\n\n"
                f"VERDICT: PASS 또는 FAIL\nFEEDBACK: [상세 피드백]"
            )
            heavy_res = await self.llm.chat(
                heavy_prompt,
                role=ModelRole.HEAVY,
                system=self._build_system_prompt("엄격한 품질 검토자입니다."),
                max_tokens=512,
                temperature=0.1,
            )
            fast_result = await fast_task
            agent_results.append(fast_result)

            fast_review  = fast_result.output
            heavy_passed = "FAIL" not in heavy_res.content.upper()

            fast_passed = fast_result.success and fast_review and fast_review.passed

            self.log.info(
                f"[{self.task_id}] Consensus — "
                f"fast={fast_passed} heavy={heavy_passed}"
            )

            if fast_passed and heavy_passed:
                # ✅ 둘 다 통과!
                return OrchestratorResult(
                    task_id=self.task_id, strategy=self.strategy,
                    domain=self.domain, passed=True, output=generated,
                    iterations=i+1, agent_results=agent_results,
                    lore=self._collect_lore(),
                )

            # 수정
            feedback = ""
            if fast_review:
                feedback = fast_review.feedback
            if not heavy_passed:
                feedback += f"\nHEAVY 검토 의견: {heavy_res.content[:200]}"

            fix_result = await self._fixer.run(
                task, {"generated": generated,
                       "review": fast_review, "iteration": i}
            )
            agent_results.append(fix_result)
            if fix_result.success:
                generated = fix_result.output

        return OrchestratorResult(
            task_id=self.task_id, strategy=self.strategy,
            domain=self.domain, passed=False, output=generated,
            iterations=self.max_iterations, agent_results=agent_results,
            lore=self._collect_lore(),
            error="CONSENSUS 검증 통과 실패",
        )

    # ── 전략 3: DEBATE ────────────────────────────────────
    async def _run_debate(
        self, task: str, context: dict
    ) -> OrchestratorResult:
        """
        FAST(효율) ↔ HEAVY(정확) 논쟁 → Orchestrator가 최선 선택
        CoOps 비즈니스 결정에 적합
        """
        agent_results = []

        # Plan
        plan_result = await self._planner.run(task, context)
        agent_results.append(plan_result)
        plan = plan_result.output if plan_result.success else None

        # Generate
        gen_result = await self._generator.run(
            task, {"plan": plan, "iteration": 0}
        )
        agent_results.append(gen_result)
        if not gen_result.success:
            return OrchestratorResult(
                task_id=self.task_id, strategy=self.strategy,
                domain=self.domain, passed=False, output=None,
                iterations=1, agent_results=agent_results,
                lore=self._collect_lore(),
                error="GeneratorAgent 실패",
            )
        generated = gen_result.output

        # FAST 관점 (효율/속도 중심)
        fast_prompt = (
            f"효율성과 실용성 관점에서 다음 결과물을 평가하세요.\n\n"
            f"작업: {task}\n결과물: {str(generated)[:800]}\n\n"
            f"VERDICT: APPROVE 또는 IMPROVE\n"
            f"ARGUMENT: [효율성 관점의 논거, 2문장]"
        )
        # HEAVY 관점 (품질/정확성 중심)
        heavy_prompt = (
            f"품질과 정확성 관점에서 다음 결과물을 평가하세요.\n\n"
            f"작업: {task}\n결과물: {str(generated)[:800]}\n\n"
            f"VERDICT: APPROVE 또는 IMPROVE\n"
            f"ARGUMENT: [품질 관점의 논거, 2문장]"
        )

        # 동시 실행
        fast_res, heavy_res = await asyncio.gather(
            self.llm.chat(fast_prompt,  role=ModelRole.FAST,
                          max_tokens=256, temperature=0.5),
            self.llm.chat(heavy_prompt, role=ModelRole.HEAVY,
                          max_tokens=256, temperature=0.3),
        )

        fast_approves  = "APPROVE" in fast_res.content.upper()
        heavy_approves = "APPROVE" in heavy_res.content.upper()

        self.log.info(
            f"[{self.task_id}] Debate — "
            f"fast_approves={fast_approves} heavy_approves={heavy_approves}"
        )

        # Orchestrator 판정
        if fast_approves and heavy_approves:
            passed = True
        elif fast_approves or heavy_approves:
            # 한쪽만 승인 → HEAVY 의견 우선 (품질 중심)
            passed = heavy_approves
        else:
            passed = False

        if not passed:
            # 수정 후 단순 pipeline으로 마무리
            debate_feedback = (
                f"FAST 의견: {fast_res.content[:150]}\n"
                f"HEAVY 의견: {heavy_res.content[:150]}"
            )
            fix_result = await self._fixer.run(
                task,
                {"generated": generated, "review": None,
                 "feedback": debate_feedback, "iteration": 0}
            )
            agent_results.append(fix_result)
            if fix_result.success:
                generated = fix_result.output
                passed = True  # Fixer 후 최선으로 승인

        return OrchestratorResult(
            task_id=self.task_id, strategy=self.strategy,
            domain=self.domain, passed=passed, output=generated,
            iterations=1, agent_results=agent_results,
            lore=self._collect_lore(),
        )

    # ── 전략 4: FASTEST ───────────────────────────────────
    async def _run_fastest(
        self, task: str, context: dict
    ) -> OrchestratorResult:
        """
        타임아웃 내 가장 먼저 응답한 결과 사용
        Fallback — 긴급 상황 UX 보호
        """
        self.log.info(
            f"[{self.task_id}] FASTEST 전략 — timeout={self._fastest_timeout}s",
        )

        try:
            gen_result = await asyncio.wait_for(
                self._generator.run(task, context),
                timeout=self._fastest_timeout,
            )
            passed = gen_result.success
            return OrchestratorResult(
                task_id=self.task_id, strategy=self.strategy,
                domain=self.domain, passed=passed,
                output=gen_result.output if passed else None,
                iterations=1,
                lore=self._collect_lore(),
                error="" if passed else gen_result.error,
            )
        except asyncio.TimeoutError:
            self.log.warning(f"[{self.task_id}] FASTEST 타임아웃 — 기본 응답 반환")
            return OrchestratorResult(
                task_id=self.task_id, strategy=self.strategy,
                domain=self.domain, passed=False, output=None,
                iterations=1, lore=self._collect_lore(),
                error=f"타임아웃 ({self._fastest_timeout}s 초과)",
            )

    # ── 내부 헬퍼 ─────────────────────────────────────────
    def _collect_lore(self) -> list[LoreEntry]:
        """모든 하위 Agent의 Lore 수집"""
        all_lore = list(self._lore)
        for agent in [self._planner, self._generator,
                      self._reviewer, self._fixer]:
            all_lore.extend(agent.lore)
        return all_lore


# ── 편의 팩토리 함수 ──────────────────────────────────────

def create_orchestrator(domain: str, strategy: str = "pipeline",
                         **kwargs) -> Orchestrator:
    """문자열로 Orchestrator 생성"""
    return Orchestrator(
        domain=OntologyDomain(domain.lower()),
        strategy=OrchestraStrategy(strategy.lower()),
        **kwargs,
    )
