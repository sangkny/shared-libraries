"""Orchestrator ↔ 4-에이전트 연동 테스트"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

from agents.base import AgentResult, AgentStatus, AgentType
from agents.feature_flags import AgentFeatureFlags
from agents.orchestrator import Orchestrator, OrchestraStrategy
from agents.planner import ExecutionPlan
from agents.reviewer import ReviewResult
from ontology.base import OntologyDomain


@pytest.fixture(autouse=True)
def _mock_four_agent(monkeypatch):
    monkeypatch.setenv("AGENT_FOUR_AGENT_MOCK", "1")


@pytest.mark.asyncio
async def test_feature_flag_routes_four_agent(monkeypatch):
    monkeypatch.setenv("AGENT_DECISION_MODE", "four_agent")
    assert AgentFeatureFlags.is_four_agent_enabled("orch-task-1")


@pytest.mark.asyncio
async def test_orchestrator_pipeline_four_agent_mock(monkeypatch):
    """Planner/Generator 스텁 + four_agent Review — LLM 없이 연동"""
    monkeypatch.setenv("AGENT_DECISION_MODE", "four_agent")

    orch = Orchestrator(
        domain=OntologyDomain.SOFTWARE,
        strategy=OrchestraStrategy.PIPELINE,
        max_iterations=1,
        task_id="orch-four-mock-1",
    )

    plan = ExecutionPlan(
        goal="add 함수",
        steps=["구현"],
        domain="software",
    )
    generated = {
        "function_name": "add_numbers",
        "parameters": ["a", "b"],
        "return_type": "int",
        "line_count": 5,
        "language": "python",
    }

    plan_result = AgentResult(
        agent_type=AgentType.PLANNER,
        task_id=orch.task_id,
        status=AgentStatus.COMPLETED,
        output=plan,
    )
    gen_result = AgentResult(
        agent_type=AgentType.GENERATOR,
        task_id=orch.task_id,
        status=AgentStatus.COMPLETED,
        output=generated,
    )

    with patch.object(orch._planner, "run", AsyncMock(return_value=plan_result)):
        with patch.object(orch._generator, "run", AsyncMock(return_value=gen_result)):
            result = await orch.execute(
                "올바른 Python add 함수를 생성하세요.",
                context={},
            )

    assert result.decision_mode == "four_agent"
    assert result.passed is True
    assert result.audit_trail.get("mode") == "four_agent"
    assert result.output == generated
    assert len(result.agent_results) >= 3


@pytest.mark.asyncio
async def test_orchestrator_pipeline_legacy_default(monkeypatch):
    """기본 legacy — four_agent 미사용"""
    monkeypatch.setenv("AGENT_DECISION_MODE", "legacy")
    monkeypatch.delenv("AGENT_FOUR_AGENT_MOCK", raising=False)

    orch = Orchestrator(
        domain=OntologyDomain.MEDICAL,
        strategy=OrchestraStrategy.PIPELINE,
        max_iterations=1,
        task_id="orch-legacy-1",
    )

    bad = {"patient_id": "P1", "ssn": "123456-1234567"}
    gen_result = AgentResult(
        agent_type=AgentType.GENERATOR,
        task_id=orch.task_id,
        status=AgentStatus.COMPLETED,
        output=bad,
    )
    plan_result = AgentResult(
        agent_type=AgentType.PLANNER,
        task_id=orch.task_id,
        status=AgentStatus.COMPLETED,
        output=ExecutionPlan(goal="검증", steps=["1"], domain="medical"),
    )

    with patch.object(orch._planner, "run", AsyncMock(return_value=plan_result)):
        with patch.object(orch._generator, "run", AsyncMock(return_value=gen_result)):
            with patch.object(
                orch._reviewer,
                "run",
                AsyncMock(
                    return_value=AgentResult(
                        agent_type=AgentType.REVIEWER,
                        task_id=orch.task_id,
                        status=AgentStatus.COMPLETED,
                        output=ReviewResult(
                            passed=False,
                            feedback="SSN 금지",
                        ),
                    )
                ),
            ):
                result = await orch.execute("환자 데이터 검증", context={})

    assert result.decision_mode == "legacy"
    assert result.passed is False


@pytest.mark.asyncio
@pytest.mark.requires_lm_studio
async def test_orchestrator_pipeline_four_agent_lm_studio(monkeypatch):
    """LM Studio 가동 시 four_agent + 실제 Advocate/Critic LLM 연동"""
    monkeypatch.setenv("AGENT_DECISION_MODE", "four_agent")
    monkeypatch.delenv("AGENT_FOUR_AGENT_MOCK", raising=False)

    orch = Orchestrator(
        domain=OntologyDomain.SOFTWARE,
        strategy=OrchestraStrategy.PIPELINE,
        max_iterations=1,
        task_id="orch-four-lm-1",
    )

    generated = {
        "function_name": "add_values",
        "parameters": ["x", "y"],
        "return_type": "int",
        "line_count": 4,
        "complexity": 1,
        "language": "python",
    }
    plan_result = AgentResult(
        agent_type=AgentType.PLANNER,
        task_id=orch.task_id,
        status=AgentStatus.COMPLETED,
        output=ExecutionPlan(goal="add", steps=["코드"], domain="software"),
    )
    gen_result = AgentResult(
        agent_type=AgentType.GENERATOR,
        task_id=orch.task_id,
        status=AgentStatus.COMPLETED,
        output=generated,
    )

    with patch.object(orch._planner, "run", AsyncMock(return_value=plan_result)):
        with patch.object(orch._generator, "run", AsyncMock(return_value=gen_result)):
            result = await orch.execute(
                "두 수를 더하는 Python 함수",
                context={},
            )

    assert result.decision_mode == "four_agent"
    assert result.audit_trail
    assert result.output is not None
    print(f"\n  decision_mode={result.decision_mode}")
    print(f"  passed={result.passed}")
    print(f"  audit keys={list(result.audit_trail.keys())}")
