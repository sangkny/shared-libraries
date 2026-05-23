#!/usr/bin/env python3
"""의료 A/B 불일치 케이스 재현."""
from __future__ import annotations

import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("AGENT_FOUR_AGENT_MOCK", "1")
os.environ.setdefault("AGENT_PIPELINE_LITE", "1")

from agents.pipeline import AgentPipeline  # noqa: E402

STRING_CASES = [
    ("경계값 신뢰도 0.65", "REVISE"),
    ("혈당 HbA1c 7.2 정기 검진", "APPROVE"),
]

STRUCTURED = [
    ({"dr_grade": 0, "confidence": 0.92, "icd10": "Z01.00"}, "APPROVE"),
    ({"dr_grade": 1, "confidence": 0.75, "icd10": "H35.01"}, "APPROVE"),
    ({"dr_grade": 2, "confidence": 0.68, "icd10": "H36.0"}, "REVISE"),
    ({"dr_grade": 3, "confidence": 0.45, "icd10": "H36.0"}, "REJECT"),
    ({"dr_grade": 4, "confidence": 0.88, "icd10": "H36.0"}, "REJECT"),
]


async def _run(pipe: AgentPipeline, artifact, expected: str) -> None:
    os.environ["AGENT_DECISION_MODE"] = "legacy"
    leg = await pipe.run_decision(artifact, "medical")
    os.environ["AGENT_DECISION_MODE"] = "four_agent"
    fa = await pipe.run_decision(artifact, "medical")
    ld, fd = leg.decision.decision, fa.decision.decision
    match = "✅" if ld == fd else "⚠️ "
    ok_l = "✓" if ld == expected else "×"
    ok_f = "✓" if fd == expected else "×"
    at = fa.audit_trail or {}
    sc = at.get("scores") or {}
    label = artifact if isinstance(artifact, str) else f"DR{artifact.get('dr_grade')} conf={artifact.get('confidence')}"
    print(f"{match} {label[:40]} exp={expected}")
    print(f"   Legacy={ld}{ok_l}  4ag={fd}{ok_f}  final={sc.get('final', fa.decision.final_score)}")
    if ld != fd:
        print(f"   → 불일치")


async def main() -> None:
    pipe = AgentPipeline()
    print("=== 문자열 케이스 (A/B 리포트) ===")
    for art, exp in STRING_CASES:
        await _run(pipe, art, exp)
    print("\n=== 구조화 DR 케이스 ===")
    for art, exp in STRUCTURED:
        await _run(pipe, art, exp)


if __name__ == "__main__":
    asyncio.run(main())
