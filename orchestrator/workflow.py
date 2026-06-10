"""
AutoNoGaDa 범용 워크플로우 — Plan→Generate→Review→Fix 루프.

MEDI-IOT에서 실증된 4-에이전트 패턴을 도메인·전략에 독립적인 API로 추상화한다.
내부 구현은 ``agents.orchestrator.Orchestrator`` (PIPELINE 전략)에 위임한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agents.orchestrator import OrchestraStrategy, Orchestrator, OrchestratorResult
from ontology.base import OntologyDomain


@dataclass
class WorkflowConfig:
    """워크플로우 실행 설정."""

    domain: OntologyDomain = OntologyDomain.SOFTWARE
    strategy: OrchestraStrategy = OrchestraStrategy.PIPELINE
    max_iterations: int = 3
    fastest_timeout_sec: float | None = None


@dataclass
class WorkflowResult:
    """Plan→Generate→Review→Fix 최종 결과."""

    passed: bool
    output: Any
    iterations: int
    total_latency_ms: float
    task_id: str = ""
    error: str = ""
    lore_count: int = 0
    agent_steps: int = 0
    raw: OrchestratorResult | None = field(default=None, repr=False)

    @classmethod
    def from_orchestrator(cls, result: OrchestratorResult) -> WorkflowResult:
        return cls(
            passed=result.passed,
            output=result.output,
            iterations=result.iterations,
            total_latency_ms=result.total_latency_ms,
            task_id=result.task_id,
            error=result.error,
            lore_count=len(result.lore),
            agent_steps=len(result.agent_results),
            raw=result,
        )


class AutoNoGaDaWorkflow:
    """
    범용 4-에이전트 워크플로우 파사드.

    사용 예::

        wf = AutoNoGaDaWorkflow()
        result = await wf.run("pytest 회귀 스크립트 추가")
        if result.passed:
            print(result.output)
    """

    def __init__(self, config: WorkflowConfig | None = None, **kwargs: Any) -> None:
        self.config = config or WorkflowConfig()
        self._kwargs = kwargs

    def _build_orchestrator(self) -> Orchestrator:
        return Orchestrator(
            domain=self.config.domain,
            strategy=self.config.strategy,
            max_iterations=self.config.max_iterations,
            fastest_timeout_sec=self.config.fastest_timeout_sec,
            **self._kwargs,
        )

    async def run(self, task: str, context: dict | None = None) -> WorkflowResult:
        """Plan→Generate→Review→Fix 루프 실행."""
        orch = self._build_orchestrator()
        raw = await orch.execute(task, context)
        return WorkflowResult.from_orchestrator(raw)

    async def run_pipeline(self, task: str, context: dict | None = None) -> WorkflowResult:
        """PIPELINE 전략 고정 실행 (AutoNoGaDa 기본)."""
        cfg = WorkflowConfig(
            domain=self.config.domain,
            strategy=OrchestraStrategy.PIPELINE,
            max_iterations=self.config.max_iterations,
            fastest_timeout_sec=self.config.fastest_timeout_sec,
        )
        orch = Orchestrator(
            domain=cfg.domain,
            strategy=cfg.strategy,
            max_iterations=cfg.max_iterations,
            fastest_timeout_sec=cfg.fastest_timeout_sec,
            **self._kwargs,
        )
        raw = await orch.execute(task, context)
        return WorkflowResult.from_orchestrator(raw)
