#!/usr/bin/env python3
"""Legacy vs 4-agent 비교 리포트 생성"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.pipeline import AgentPipeline  # noqa: E402

CASES = [
    ("올바른 Python 코드", "software", "APPROVE"),
    ("IOP=25 안저 데이터", "iot", "REJECT"),
    ("PII 포함 의료 기록", "medical", "REJECT"),
    ("정상 결재 요청", "business", "APPROVE"),
    ("경계값 신뢰도 0.65", "medical", "REVISE"),
]


async def _run_mode(mode: str) -> dict:
    os.environ["AGENT_DECISION_MODE"] = mode
    os.environ.setdefault("AGENT_FOUR_AGENT_MOCK", "1")
    os.environ.setdefault("AGENT_PIPELINE_LITE", "1")
    pipe = AgentPipeline()
    latencies: list[float] = []
    decisions: dict[str, int] = {}
    passed = 0
    for inp, domain, expected in CASES:
        t0 = time.perf_counter()
        out = await pipe.run(inp, domain, request_id=f"rpt-{mode}-{domain}")
        latencies.append((time.perf_counter() - t0) * 1000)
        dec = out.decision.decision
        decisions[dec] = decisions.get(dec, 0) + 1
        if dec == expected:
            passed += 1
    avg_ms = sum(latencies) / len(latencies) if latencies else 0.0
    return {
        "total_tests": len(CASES),
        "passed": passed,
        "avg_latency_ms": round(avg_ms, 1),
        "decisions": decisions,
    }


async def main() -> int:
    legacy = await _run_mode("legacy")
    four = await _run_mode("four_agent")

    disagreements = []
    os.environ["AGENT_DECISION_MODE"] = "legacy"
    os.environ.setdefault("AGENT_FOUR_AGENT_MOCK", "1")
    pipe = AgentPipeline()
    for inp, domain, _ in CASES:
        os.environ["AGENT_DECISION_MODE"] = "legacy"
        leg = await pipe.run(inp, domain)
        os.environ["AGENT_DECISION_MODE"] = "four_agent"
        fa = await pipe.run(inp, domain)
        if leg.decision.decision != fa.decision.decision:
            disagreements.append(
                {
                    "input": inp,
                    "domain": domain,
                    "legacy": leg.decision.decision,
                    "four_agent": fa.decision.decision,
                    "note": "도메인 임계값·듀얼 관점 차이",
                }
            )

    total = len(CASES)
    agree = total - len(disagreements)
    agreement_rate = round(agree / total, 2) if total else 1.0
    recommendation = "four_agent" if agreement_rate >= 0.6 else "legacy"

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legacy": legacy,
        "four_agent": four,
        "agreement_rate": agreement_rate,
        "disagreements": disagreements,
        "recommendation": recommendation,
        "rollback_tag": "before-four-agent-v1.0",
    }

    reports_dir = ROOT / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    out = reports_dir / f"four_agent_comparison_{stamp}.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
