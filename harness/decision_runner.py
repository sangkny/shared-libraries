# harness/decision_runner.py — DECISION 시나리오 (4-에이전트 A/B)
"""harness/scenarios/decision_scenarios.json 기반 Pipeline 결정 검증."""
from __future__ import annotations

import json
import logging
import time
from typing import Any
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from agents.pipeline import AgentPipeline

log = logging.getLogger("harness.decision")

_SCENARIOS_PATH = Path(__file__).resolve().parent / "scenarios" / "decision_scenarios.json"


@dataclass
class DecisionScenarioResult:
    name: str
    domain: str
    expected: str
    legacy_decision: str
    four_agent_decision: str
    match: bool
    passed: bool
    latency_ms: float
    error: str = ""

    @property
    def summary(self) -> str:
        icon = "✅" if self.passed else "❌"
        m = "✓" if self.match else "≠"
        return (
            f"{icon} [{self.domain}] {self.name[:30]} "
            f"legacy={self.legacy_decision} 4ag={self.four_agent_decision} "
            f"exp={self.expected} {m} {self.latency_ms:.0f}ms"
        )


@dataclass
class DecisionHarnessReport:
    results: list[DecisionScenarioResult] = field(default_factory=list)
    agreement_rate: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def pass_rate(self) -> float:
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.passed) / len(self.results)

    def print_report(self) -> None:
        print("\n" + "=" * 60)
        print("  Harness DECISION (4-agent vs legacy)")
        print("=" * 60)
        for r in self.results:
            print(f"  {r.summary}")
        print(f"\n  agreement_rate: {self.agreement_rate:.1%}")
        print(f"  pass_rate (expected match): {self.pass_rate:.1%}")
        print("=" * 60 + "\n")


def _load_cases() -> list[dict[str, Any]]:
    if not _SCENARIOS_PATH.is_file():
        return []
    data = json.loads(_SCENARIOS_PATH.read_text(encoding="utf-8"))
    return list(data.get("cases") or [])


async def run_decision_harness() -> DecisionHarnessReport:
    import os

    os.environ.setdefault("AGENT_FOUR_AGENT_MOCK", "1")
    os.environ.setdefault("AGENT_PIPELINE_LITE", "1")
    pipe = AgentPipeline()
    report = DecisionHarnessReport()
    cases = _load_cases()
    if not cases:
        log.warning("No decision scenarios at %s", _SCENARIOS_PATH)
        return report

    def _case_label(case: dict[str, Any], inp: Any) -> str:
        if case.get("label"):
            return str(case["label"])[:40]
        if isinstance(inp, str):
            return inp[:40]
        if isinstance(inp, dict):
            fn = inp.get("function_name")
            return str(fn or "structured")[:40]
        return str(inp)[:40]

    matches = 0
    for i, case in enumerate(cases):
        inp = case["input"]
        domain = case["domain"]
        expected = case.get("expected", "APPROVE")
        label = _case_label(case, inp)
        t0 = time.monotonic()
        try:
            import os as _os

            _os.environ["AGENT_DECISION_MODE"] = "legacy"
            leg = await pipe.run(inp, domain, request_id=f"h-leg-{i}")
            _os.environ["AGENT_DECISION_MODE"] = "four_agent"
            fa = await pipe.run(inp, domain, request_id=f"h-4ag-{i}")
            ld = leg.decision.decision
            fd = fa.decision.decision
            match = ld == fd
            if match:
                matches += 1
            passed = fd == expected or ld == expected
            report.results.append(
                DecisionScenarioResult(
                    name=label,
                    domain=domain,
                    expected=expected,
                    legacy_decision=ld,
                    four_agent_decision=fd,
                    match=match,
                    passed=passed,
                    latency_ms=(time.monotonic() - t0) * 1000,
                )
            )
        except Exception as exc:
            report.results.append(
                DecisionScenarioResult(
                    name=label,
                    domain=domain,
                    expected=expected,
                    legacy_decision="ERR",
                    four_agent_decision="ERR",
                    match=False,
                    passed=False,
                    latency_ms=0,
                    error=str(exc)[:200],
                )
            )

    n = len(cases)
    report.agreement_rate = matches / n if n else 1.0
    return report
