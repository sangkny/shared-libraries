"""4-에이전트 LM Studio 실연동 — Advocate / Critic / Pipeline"""
from __future__ import annotations

import json
import os

import pytest

from agents.feature_flags import AgentFeatureFlags
from agents.pipeline import AgentPipeline
from agents.reviewer import AdvocateReviewer, CriticReviewer
from ontology.base import OntologyDomain


ARTIFACT = {
    "function_name": "safe_divide",
    "parameters": ["a", "b"],
    "return_type": "float",
    "line_count": 8,
    "complexity": 2,
    "language": "python",
}


@pytest.mark.asyncio
@pytest.mark.requires_lm_studio
async def test_advocate_real_llm(monkeypatch):
    monkeypatch.delenv("AGENT_FOUR_AGENT_MOCK", raising=False)
    adv = AdvocateReviewer()
    report = await adv.review(ARTIFACT, {"domain": "software"})
    assert report.confidence >= 0.0
    assert report.reasons
    print(f"\n  advocate confidence={report.confidence}")


@pytest.mark.asyncio
@pytest.mark.requires_lm_studio
async def test_critic_real_llm(monkeypatch):
    monkeypatch.delenv("AGENT_FOUR_AGENT_MOCK", raising=False)
    critic = CriticReviewer()
    report = await critic.review(ARTIFACT, {"domain": "software"})
    assert report.risk_score >= 0.0
    assert report.issues
    print(f"\n  critic risk={report.risk_score}")


@pytest.mark.asyncio
@pytest.mark.requires_lm_studio
async def test_full_pipeline_real_llm(monkeypatch):
    monkeypatch.setenv("AGENT_DECISION_MODE", "four_agent")
    monkeypatch.delenv("AGENT_FOUR_AGENT_MOCK", raising=False)
    pipe = AgentPipeline(domain=OntologyDomain.SOFTWARE, task_id="lm-full-1")
    out = await pipe.run_decision(ARTIFACT, "software", request_id="lm-full-1")
    assert out.mode == "four_agent"
    assert out.decision.decision in ("APPROVE", "REVISE", "REJECT")
    assert out.audit_trail.get("mode") == "four_agent"
    print(f"\n  decision={out.decision.decision} score={out.decision.final_score}")
    print(f"  audit={json.dumps(out.audit_trail, ensure_ascii=False)[:300]}")
