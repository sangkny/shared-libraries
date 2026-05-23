# shared-libraries/agents/decision_gate.py
"""최종 결정자 — 도메인별 임계값 기반 (DecisionGate)"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from .feature_flags import AgentFeatureFlags
from .four_agent_types import DecisionResult, MediationResult


class DecisionGate:
    """최종 결정자 — 도메인별 임계값 기반"""

    THRESHOLDS = {
        "medical":   {"approve": 0.80, "revise": 0.60},
        "software":  {"approve": 0.70, "revise": 0.50},
        "business":  {"approve": 0.65, "revise": 0.45},
        "iot":       {"approve": 0.80, "revise": 0.60},
        "iot_device": {"approve": 0.80, "revise": 0.60},
        "knowledge": {"approve": 0.70, "revise": 0.50},
    }

    @staticmethod
    def _normalize_domain(domain: str) -> str:
        d = (domain or "software").strip().lower()
        if d in ("iot_device", "health_data"):
            return "iot"
        return d

    def decide(self, mediation: MediationResult, domain: str) -> DecisionResult:
        key = self._normalize_domain(domain)
        t = self.THRESHOLDS.get(key, {"approve": 0.70, "revise": 0.50})
        s = mediation.final_score

        if s >= t["approve"]:
            decision, action = "APPROVE", "auto_promote"
        elif s >= t["revise"]:
            decision, action = "REVISE", "request_revision"
        else:
            decision, action = "REJECT", "block"

        audit: dict = {}
        if AgentFeatureFlags.audit_trail_enabled():
            audit = self._build_audit_trail(mediation, decision)

        return DecisionResult(
            decision=decision,
            action=action,
            final_score=s,
            advocate_score=mediation.advocate_score,
            critic_score=mediation.critic_score,
            ontology_issues=list(mediation.ontology_issues),
            audit_trail=audit,
        )

    def _build_audit_trail(self, m: MediationResult, decision: str) -> dict:
        adv_sum = m.advocate_report.summary if m.advocate_report else ""
        crt_sum = m.critic_report.summary if m.critic_report else ""
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": "four_agent",
            "domain": m.domain,
            "decision": decision,
            "scores": {
                "advocate": m.advocate_score,
                "critic": m.critic_score,
                "final": m.final_score,
            },
            "weights": m.weights,
            "ontology_issues": m.ontology_issues,
            "advocate_summary": adv_sum,
            "critic_summary": crt_sum,
        }
