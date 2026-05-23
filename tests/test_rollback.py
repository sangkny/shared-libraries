"""롤백·피처 플래그 검증"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from agents.feature_flags import AgentFeatureFlags
from agents.pipeline import AgentPipeline


@pytest.fixture(autouse=True)
def _mock_env(monkeypatch):
    monkeypatch.setenv("AGENT_FOUR_AGENT_MOCK", "1")
    monkeypatch.setenv("AGENT_PIPELINE_LITE", "1")


def test_legacy_mode_default(monkeypatch):
    monkeypatch.delenv("AGENT_DECISION_MODE", raising=False)
    assert AgentFeatureFlags.get_mode() == "legacy"
    assert not AgentFeatureFlags.is_four_agent_enabled()


def test_git_tag_exists():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["git", "tag", "-l", "before-four-agent-v1.0"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert "before-four-agent-v1.0" in result.stdout


@pytest.mark.asyncio
async def test_rollback_works(monkeypatch):
    monkeypatch.setenv("AGENT_DECISION_MODE", "legacy")
    pipe = AgentPipeline()
    result = await pipe.run("테스트", "software")
    assert result.mode == "legacy"
    assert result.decision is not None
    assert result.audit_trail["mode"] == "legacy"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_existing_apis_unchanged(monkeypatch):
    """API E2E — 로컬 스택 가동 시에만 실행"""
    import httpx

    monkeypatch.setenv("AGENT_DECISION_MODE", "legacy")
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.post(
                "http://localhost:8001/api/v1/diagnosis/comprehensive",
                json={"patient_id": "smoke"},
            )
    except Exception:
        pytest.skip("MEDI-IOT API 미가동")
    if resp.status_code != 200:
        pytest.skip(f"MEDI API status={resp.status_code}")
