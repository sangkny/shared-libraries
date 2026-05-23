#!/usr/bin/env python3
"""ROLLOUT 100% / four_agent 전환 체크리스트."""
from __future__ import annotations

import json
import subprocess
import sys
from glob import glob
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    checks: list[tuple[str, bool, str]] = []
    reports = sorted(glob(str(ROOT / "reports" / "ab_comparison_*.json")))
    rate = 0.0
    medical_n = 0
    if reports:
        data = json.loads(Path(reports[-1]).read_text(encoding="utf-8"))
        rate = float(data.get("agreement_rate", 0))
        medical_n = sum(
            1 for d in data.get("disagreements", []) if d.get("domain") == "medical"
        )
    checks.append(("agreement_rate >= 0.90", rate >= 0.90, f"{rate:.1%}"))
    checks.append(("의료 불일치 0건", medical_n == 0, f"{medical_n}건"))

    r = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_four_agent_pipeline.py",
         "tests/test_ab_comparison.py", "tests/test_rollback.py", "-q", "--tb=no"],
        cwd=ROOT,
        env={**dict(__import__("os").environ), "PYTHONPATH": str(ROOT),
             "AGENT_FOUR_AGENT_MOCK": "1"},
        capture_output=True,
        text=True,
    )
    ok_tests = r.returncode == 0
    tail = (r.stdout or "").strip().split("\n")[-1] if r.stdout else str(r.returncode)
    checks.append(("four-agent 단위·rollback", ok_tests, tail[:50]))

    print("\n=== ROLLOUT 100% 전환 체크리스트 ===")
    all_ok = True
    for name, ok, detail in checks:
        print(f"  {'✅' if ok else '❌'} {name}: {detail}")
        all_ok = all_ok and ok
    print(f"\n결론: {'ROLLOUT=100% / four_agent 전환 가능' if all_ok else '조건 미충족'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
