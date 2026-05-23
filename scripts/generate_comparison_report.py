#!/usr/bin/env python3
"""Legacy vs 4-agent 비교 리포트 생성"""
from __future__ import annotations

import argparse
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

_SOFTWARE_ADD = {
    "function_name": "add",
    "parameters": ["a", "b"],
    "return_type": "int",
    "line_count": 3,
    "complexity": 1,
    "parameter_count": 2,
    "nesting_depth": 1,
    "language": "python",
    "is_async": False,
    "has_return_value": True,
}

CASES = [
    (_SOFTWARE_ADD, "software", "APPROVE"),
    ("def add(a,b): return a+b", "software", "APPROVE"),
    ("IOP=25 안저 데이터", "iot", "REJECT"),
    ("IOP=25 device reading", "iot_device", "REJECT"),
    ("PII 포함 의료 기록 주민번호", "medical", "REJECT"),
    ("정상 결재 요청 승인", "business", "APPROVE"),
    ("경계값 신뢰도 0.65", "medical", "REVISE"),
    ("혈당 HbA1c 7.2 정기 검진", "medical", "APPROVE"),
    ("async def fetch(): pass", "software", "APPROVE"),
    ("금지 필드 password inline", "software", "REVISE"),
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
        rid = hash(repr(inp)) % 1000
        out = await pipe.run(inp, domain, request_id=f"rpt-{mode}-{domain}-{rid}")
        latencies.append((time.perf_counter() - t0) * 1000)
        dec = out.decision.decision
        decisions[dec] = decisions.get(dec, 0) + 1
        if dec == expected:
            passed += 1
    n = len(CASES)
    return {
        "total_tests": n,
        "passed": passed,
        "pass_rate": round(passed / n, 4) if n else 1.0,
        "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0.0,
        "decisions": decisions,
    }


async def build_report() -> dict:
    legacy = await _run_mode("legacy")
    four = await _run_mode("four_agent")

    disagreements = []
    os.environ.setdefault("AGENT_FOUR_AGENT_MOCK", "1")
    pipe = AgentPipeline()
    for inp, domain, expected in CASES:
        os.environ["AGENT_DECISION_MODE"] = "legacy"
        leg = await pipe.run(inp, domain, request_id=f"cmp-leg-{domain}")
        os.environ["AGENT_DECISION_MODE"] = "four_agent"
        fa = await pipe.run(inp, domain, request_id=f"cmp-4ag-{domain}")
        if leg.decision.decision != fa.decision.decision:
            disagreements.append(
                {
                    "input": (inp if isinstance(inp, str) else repr(inp))[:60],
                    "domain": domain,
                    "expected": expected,
                    "legacy": leg.decision.decision,
                    "four_agent": fa.decision.decision,
                    "note": "도메인 임계값·듀얼 관점 차이",
                }
            )

    total = len(CASES)
    agree = total - len(disagreements)
    agreement_rate = round(agree / total, 4) if total else 1.0
    if agreement_rate >= 0.85:
        recommendation = "four_agent"
    elif agreement_rate >= 0.65:
        recommendation = "gradual_rollout"
    else:
        recommendation = "legacy"

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "test_cases": total,
        "legacy": legacy,
        "four_agent": four,
        "agreement_rate": agreement_rate,
        "disagreements": disagreements,
        "recommendation": recommendation,
        "rollback_tag": "before-four-agent-v1.0",
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description="Legacy vs 4-agent A/B report")
    parser.add_argument(
        "--output",
        type=str,
        default="",
        help="Output JSON path (default: reports/ab_comparison_YYYYMMDD.json)",
    )
    args = parser.parse_args()

    report = await build_report()
    reports_dir = ROOT / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    if args.output:
        out = Path(args.output)
        if not out.is_absolute():
            out = ROOT / out
    else:
        stamp = datetime.now().strftime("%Y%m%d")
        out = reports_dir / f"ab_comparison_{stamp}.json"

    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nWrote {out}")
    print(f"일치율: {report['agreement_rate']:.1%}")
    print(f"불일치: {len(report['disagreements'])}건")
    print(f"권장: {report['recommendation']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
