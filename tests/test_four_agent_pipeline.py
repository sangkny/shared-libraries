"""4-에이전트 파이프라인 단위 테스트"""
from __future__ import annotations

import os

import pytest

from agents.decision_gate import DecisionGate
from agents.feature_flags import AgentFeatureFlags
from agents.four_agent_types import AdvocateReport, CriticReport
from agents.pipeline import AgentPipeline
from ontology.validator import OntologyValidator


@pytest.fixture(autouse=True)
def _mock_env(monkeypatch):
    monkeypatch.setenv("AGENT_FOUR_AGENT_MOCK", "1")
    monkeypatch.setenv("AGENT_PIPELINE_LITE", "1")


@pytest.mark.asyncio
async def test_four_agent_path_mode(monkeypatch):
    monkeypatch.setenv("AGENT_DECISION_MODE", "four_agent")
    pipe = AgentPipeline(domain="software")
    out = await pipe.run("올바른 Python 코드", domain="software", request_id="t1")
    assert out.mode == "four_agent"
    assert out.decision.decision == "APPROVE"
    assert out.audit_trail.get("mode") == "four_agent"


@pytest.mark.asyncio
async def test_mediate_and_gate():
    validator = OntologyValidator.for_domain("medical")
    advocate = AdvocateReport(confidence=0.65, summary="ok")
    critic = CriticReport(risk_score=0.35, summary="borderline")
    mediation = validator.mediate(advocate, critic, "medical")
    decision = DecisionGate().decide(mediation, "medical")
    assert decision.decision == "REVISE"


@pytest.mark.asyncio
async def test_iot_reject(monkeypatch):
    monkeypatch.setenv("AGENT_DECISION_MODE", "four_agent")
    pipe = AgentPipeline(domain="iot")
    out = await pipe.run("IOP=25 안저 데이터", domain="iot", request_id="iot-1")
    assert out.decision.decision == "REJECT"
