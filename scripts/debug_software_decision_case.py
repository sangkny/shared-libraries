#!/usr/bin/env python3
"""Harness software 케이스 — legacy / 4-agent LLM 응답 진단."""
from __future__ import annotations

import asyncio
import json
import os
import sys

# 프로젝트 루트
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("AGENT_PIPELINE_LITE", "1")
os.environ["AGENT_FOUR_AGENT_MOCK"] = "0"
os.environ["AGENT_DECISION_MODE"] = "four_agent"

from agents.pipeline import AgentPipeline
from agents.reviewer import AdvocateReviewer, CriticReviewer, ReviewerAgent
from ontology.base import OntologyDomain
from ontology.validator import OntologyValidator


ARTIFACT = {
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
DOMAIN = "software"


SAMPLE_CODE = '''def add(a: int, b: int) -> int:
    """Return sum."""
    return a + b'''


async def main() -> None:
    pipe = AgentPipeline(domain=OntologyDomain.SOFTWARE)
    ctx = {"domain": DOMAIN, "generated": ARTIFACT}
    val = OntologyValidator.for_domain(DOMAIN)
    for label, art in [("meta", ARTIFACT), ("code", SAMPLE_CODE)]:
        r = await val.validate({"payload": art})
        print(f"ontology[{label}]: passed={r.passed} errors={[e.message[:60] for e in r.errors[:3]]}")

    print("=== env ===")
    print("MOCK=", os.getenv("AGENT_FOUR_AGENT_MOCK"))
    print("LITE=", os.getenv("AGENT_PIPELINE_LITE"))

    # Legacy reviewer
    os.environ["AGENT_DECISION_MODE"] = "legacy"
    rev = ReviewerAgent(domain=OntologyDomain.SOFTWARE)
    r = await rev.run(str(ARTIFACT)[:500], ctx)
    review = r.output
    valid = await OntologyValidator.for_domain(DOMAIN).validate({"payload": ARTIFACT})
    print("\n=== Legacy ReviewerAgent ===")
    print("passed:", review.passed if review else None)
    print("feedback:", (review.feedback or "")[:300] if review else "")
    print("llm_review:", (review.llm_review or "")[:400] if review else "")
    print("ontology:", valid.passed, valid.summary if hasattr(valid, "summary") else "")

    leg = await pipe.run(ARTIFACT, DOMAIN, request_id="debug-leg")
    print("legacy decision:", leg.decision.decision, "score:", leg.decision.final_score)

    # 4-agent raw
    adv = AdvocateReviewer()
    crt = CriticReviewer()
    ar = await adv.review(ARTIFACT, ctx)
    cr = await crt.review(ARTIFACT, ctx)
    print("\n=== Advocate ===")
    print(json.dumps(ar.__dict__, ensure_ascii=False, indent=2))
    print("\n=== Critic ===")
    print(json.dumps(cr.__dict__, ensure_ascii=False, indent=2))

    med = OntologyValidator.for_domain(DOMAIN).mediate(ar, cr, DOMAIN, ARTIFACT)
    print("\n=== Mediation ===")
    print(
        "adv_score=", med.advocate_score,
        "crt_score=", med.critic_score,
        "final=", med.final_score,
        "issues=", med.ontology_issues,
    )

    os.environ["AGENT_DECISION_MODE"] = "four_agent"
    fa = await pipe.run(ARTIFACT, DOMAIN, request_id="debug-4ag")
    print("\n=== four_agent decision ===")
    print(fa.decision.decision, fa.decision.final_score)
    print("audit:", json.dumps(fa.audit_trail or {}, ensure_ascii=False)[:500])


if __name__ == "__main__":
    asyncio.run(main())
