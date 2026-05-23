# shared-libraries/agents/pipeline.py
"""피처 플래그 기반 듀얼 모드 AgentPipeline — legacy | four_agent"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

from llm.client import LLMClient
from ontology.base import OntologyDomain
from ontology.validator import OntologyValidator

from .decision_gate import DecisionGate
from .feature_flags import AgentFeatureFlags
from .fixer import FixerAgent
from .four_agent_types import DecisionResult, PipelineResult
from .generator import GeneratorAgent
from .planner import PlannerAgent
from .reviewer import AdvocateReviewer, CriticReviewer, ReviewerAgent

log = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lite_pipeline() -> bool:
    return os.getenv("AGENT_PIPELINE_LITE", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


class AgentPipeline:
    """피처 플래그 기반 듀얼 모드 파이프라인"""

    def __init__(
        self,
        domain: str | OntologyDomain = OntologyDomain.SOFTWARE,
        llm: LLMClient | None = None,
        task_id: str | None = None,
    ):
        if isinstance(domain, OntologyDomain):
            self._domain_enum = domain
            self._domain_str = domain.value
        else:
            self._domain_str = (domain or "software").lower()
            try:
                self._domain_enum = OntologyDomain(self._domain_str)
            except ValueError:
                self._domain_enum = OntologyDomain.GENERAL
                self._domain_str = "general"

        self.task_id = task_id or "pipeline"
        self.llm = llm or LLMClient()

        self.planner = PlannerAgent(
            domain=self._domain_enum, llm=self.llm, task_id=self.task_id
        )
        self.generator = GeneratorAgent(
            domain=self._domain_enum, llm=self.llm, task_id=self.task_id
        )
        self.fixer = FixerAgent(
            domain=self._domain_enum, llm=self.llm, task_id=self.task_id
        )
        self.reviewer = ReviewerAgent(
            domain=self._domain_enum, llm=self.llm, task_id=self.task_id
        )
        self.validator = OntologyValidator.for_domain(self._domain_str)
        self.advocate = AdvocateReviewer(llm=self.llm, task_id=self.task_id)
        self.critic = CriticReviewer(llm=self.llm, task_id=self.task_id)
        self.gate = DecisionGate()

    async def run(
        self,
        task: str,
        domain: str | None = None,
        request_id: str | None = None,
    ) -> PipelineResult:
        domain_eff = (domain or self._domain_str).lower()
        req_id = request_id or self.task_id

        if _lite_pipeline():
            artifact: Any = task
        else:
            plan_result = await self.planner.run(task, {"domain": domain_eff})
            plan = plan_result.output if plan_result.success else None
            gen_result = await self.generator.run(
                task, {"plan": plan, "domain": domain_eff}
            )
            artifact = gen_result.output if gen_result.success else task
            if getattr(artifact, "has_errors", False):
                fix_result = await self.fixer.run(
                    task, {"generated": artifact, "domain": domain_eff}
                )
                if fix_result.success:
                    artifact = fix_result.output

        return await self.run_decision(artifact, domain_eff, req_id)

    async def run_decision(
        self,
        artifact: Any,
        domain: str | None = None,
        request_id: str | None = None,
    ) -> PipelineResult:
        """생성물에 대한 legacy / four-agent 결정만 실행 (Orchestrator 연동용)"""
        domain_eff = (domain or self._domain_str).lower()
        req_id = request_id or self.task_id
        if AgentFeatureFlags.is_four_agent_enabled(req_id):
            return await self._four_agent_path(artifact, domain_eff, req_id)
        return await self._legacy_path(artifact, domain_eff, req_id)

    async def _legacy_path(
        self, result: Any, domain: str, request_id: str
    ) -> PipelineResult:
        """기존 방식 — ReviewerAgent + OntologyValidator"""
        if os.getenv("AGENT_FOUR_AGENT_MOCK", "").strip().lower() in (
            "1",
            "true",
            "yes",
        ):
            return self._legacy_mock_path(result, domain, request_id)

        rev_result = await self.reviewer.run(
            str(result)[:500],
            {"generated": result, "domain": domain},
        )
        review = rev_result.output
        valid = await self.validator.validate(
            result if isinstance(result, dict) else {"payload": str(result)}
        )

        legacy_decision = self._legacy_decision(review, valid)
        audit = {
            "mode": "legacy",
            "timestamp": _utc_now_iso(),
            "request_id": request_id,
            "passed": bool(review and review.passed and valid.passed),
        }
        if AgentFeatureFlags.audit_trail_enabled():
            audit["ontology_passed"] = valid.passed
            audit["review_passed"] = bool(review and review.passed)

        return PipelineResult(
            result=result,
            decision=legacy_decision,
            mode="legacy",
            audit_trail=audit,
        )

    def _legacy_mock_path(
        self, result: Any, domain: str, request_id: str
    ) -> PipelineResult:
        """단위/A/B 테스트용 결정론적 legacy 경로 (Reviewer LLM 미사용)"""
        text = str(result or "").lower()
        dom = domain.lower()
        decision = "APPROVE"
        action = "auto_promote"
        score = 0.85

        if dom == "medical" and any(k in text for k in ("pii", "주민")):
            decision, action, score = "REJECT", "block", 0.2
        elif dom in ("iot", "iot_device") and "iop" in text and "25" in text:
            decision, action, score = "REJECT", "block", 0.25
        elif dom == "medical" and ("경계값" in text or "0.65" in text):
            decision, action, score = "APPROVE", "auto_promote", 0.72
        elif "python" in text or "올바른" in text:
            decision, action, score = "APPROVE", "auto_promote", 0.9
        elif "결재" in text:
            decision, action, score = "APPROVE", "auto_promote", 0.88

        dr = DecisionResult(
            decision=decision,
            action=action,
            final_score=score,
            advocate_score=0.0,
            critic_score=0.0,
            audit_trail={"mode": "legacy", "mock": True},
        )
        audit = {
            "mode": "legacy",
            "timestamp": _utc_now_iso(),
            "request_id": request_id,
            "mock": True,
        }
        return PipelineResult(
            result=result,
            decision=dr,
            mode="legacy",
            audit_trail=audit,
        )

    def _legacy_decision(self, review: Any, valid: Any) -> DecisionResult:
        passed = bool(review and getattr(review, "passed", False) and valid.passed)
        if passed:
            decision = "APPROVE"
            action = "auto_promote"
            score = 1.0
        elif valid.error_count > 0:
            decision = "REJECT"
            action = "block"
            score = 0.2
        else:
            decision = "REVISE"
            action = "request_revision"
            score = 0.4
        return DecisionResult(
            decision=decision,
            action=action,
            final_score=score,
            advocate_score=0.0,
            critic_score=0.0,
            audit_trail={"mode": "legacy"},
        )

    async def _four_agent_path(
        self, result: Any, domain: str, request_id: str
    ) -> PipelineResult:
        """4-에이전트 — Advocate + Critic → mediate → DecisionGate"""
        ctx = {"domain": domain, "request_id": request_id}
        advocate_r, critic_r = await asyncio.gather(
            self.advocate.review(result, ctx),
            self.critic.review(result, ctx),
        )
        mediation = self.validator.mediate(advocate_r, critic_r, domain, result)
        decision = self.gate.decide(mediation, domain)
        audit = decision.audit_trail or {}
        audit.setdefault("request_id", request_id)
        return PipelineResult(
            result=result,
            decision=decision,
            mode="four_agent",
            audit_trail=audit,
        )
