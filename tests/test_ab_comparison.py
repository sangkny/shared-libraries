"""Legacy vs 4-에이전트 A/B 비교 테스트"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agents.pipeline import AgentPipeline


TEST_CASES = [
    ("올바른 Python 코드", "software", "APPROVE"),
    ("IOP=25 안저 데이터", "iot", "REJECT"),
    ("PII 포함 의료 기록", "medical", "REJECT"),
    ("정상 결재 요청", "business", "APPROVE"),
    ("경계값 신뢰도 0.65", "medical", "REVISE"),
]


@pytest.fixture(autouse=True)
def _mock_env(monkeypatch):
    monkeypatch.setenv("AGENT_FOUR_AGENT_MOCK", "1")
    monkeypatch.setenv("AGENT_PIPELINE_LITE", "1")


@pytest.mark.asyncio
async def test_compare_decisions(monkeypatch, tmp_path):
    pipe = AgentPipeline()
    results = []
    for input_data, domain, expected in TEST_CASES:
        monkeypatch.setenv("AGENT_DECISION_MODE", "legacy")
        legacy = await pipe.run(input_data, domain, request_id=f"leg-{domain}")

        monkeypatch.setenv("AGENT_DECISION_MODE", "four_agent")
        four = await pipe.run(input_data, domain, request_id=f"4ag-{domain}")

        results.append(
            {
                "input": input_data,
                "domain": domain,
                "expected": expected,
                "legacy_decision": legacy.decision.decision,
                "four_agent_decision": four.decision.decision,
                "legacy_mode": legacy.mode,
                "four_agent_mode": four.mode,
                "match": legacy.decision.decision == four.decision.decision,
            }
        )

    reports_dir = Path(__file__).resolve().parents[1] / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    out_path = reports_dir / "ab_comparison.json"
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    for r in results:
        assert r["four_agent_decision"] in ("APPROVE", "REVISE", "REJECT")
        assert r["legacy_decision"] in ("APPROVE", "REVISE", "REJECT")


@pytest.mark.asyncio
async def test_feature_flag_ab_split(monkeypatch):
    monkeypatch.setenv("AGENT_DECISION_MODE", "ab_test")
    monkeypatch.setenv("AGENT_FOUR_AGENT_ROLLOUT", "50")
    pipe = AgentPipeline()
    legacy_count = 0
    four_count = 0
    for i in range(100):
        result = await pipe.run("테스트", "software", request_id=f"req-{i}")
        if result.mode == "legacy":
            legacy_count += 1
        else:
            four_count += 1
    assert 35 <= four_count <= 65, f"A/B 분기 이상: 4-에이전트={four_count}%"
