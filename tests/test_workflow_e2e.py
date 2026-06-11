"""AutoNoGaDaWorkflow mock E2E — LM Studio 불필요."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agents.base import AgentResult, AgentStatus, AgentType
from agents.planner import ExecutionPlan
from agents.reviewer import ReviewResult
from orchestrator.workflow import AutoNoGaDaWorkflow


def _ok(agent_type: AgentType, output, task_id: str = "wf-mock") -> AgentResult:
    return AgentResult(
        agent_type=agent_type,
        task_id=task_id,
        status=AgentStatus.COMPLETED,
        output=output,
    )


@pytest.fixture
def workflow() -> AutoNoGaDaWorkflow:
    return AutoNoGaDaWorkflow(task_id="wf-mock")


@pytest.mark.asyncio
async def test_workflow_plan_step(workflow: AutoNoGaDaWorkflow):
    plan = ExecutionPlan(goal="hello", steps=["def hello(): pass"], domain="software")
    orch = workflow._build_orchestrator()
    with patch.object(orch._planner, "run", AsyncMock(return_value=_ok(AgentType.PLANNER, plan))):
        result = await workflow.plan("hello world 함수")
    assert result.success
    assert result.output.steps


@pytest.mark.asyncio
async def test_workflow_generate_step(workflow: AutoNoGaDaWorkflow):
    code = "def hello():\n    return 'hello'"
    orch = workflow._build_orchestrator()
    with patch.object(orch._generator, "run", AsyncMock(return_value=_ok(AgentType.GENERATOR, code))):
        result = await workflow.generate("task", plan={"steps": []})
    assert result.success
    assert "hello" in result.output


@pytest.mark.asyncio
async def test_workflow_review_step(workflow: AutoNoGaDaWorkflow):
    review = ReviewResult(passed=True, feedback="ok")
    orch = workflow._build_orchestrator()
    with patch.object(orch._reviewer, "run", AsyncMock(return_value=_ok(AgentType.REVIEWER, review))):
        result = await workflow.review("task", generated="code")
    assert result.success
    assert result.output.passed is True


@pytest.mark.asyncio
async def test_workflow_fix_step(workflow: AutoNoGaDaWorkflow):
    fixed = "def hello():\n    return 'hello'\n"
    orch = workflow._build_orchestrator()
    with patch.object(orch._fixer, "run", AsyncMock(return_value=_ok(AgentType.FIXER, fixed))):
        result = await workflow.fix("task", generated="bad", review={"hints": ["syntax"]})
    assert result.success
    assert "hello" in result.output


@pytest.mark.asyncio
async def test_workflow_run_pipeline_mock(workflow: AutoNoGaDaWorkflow):
    from agents.orchestrator import OrchestratorResult, OrchestraStrategy
    from ontology.base import OntologyDomain

    mock_result = OrchestratorResult(
        task_id="wf-mock",
        strategy=OrchestraStrategy.PIPELINE,
        domain=OntologyDomain.SOFTWARE,
        passed=True,
        output="def hello(): return 'hi'",
        iterations=1,
    )
    orch = workflow._build_orchestrator()
    with patch.object(orch, "execute", AsyncMock(return_value=mock_result)):
        result = await workflow.run("간단한 hello world")
    assert result.passed
    assert result.output
