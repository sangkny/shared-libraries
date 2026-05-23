# shared-libraries/agents/four_agent_types.py
"""4-에이전트 결정 파이프라인 공용 타입"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AdvocateReport:
    reasons: list[str] = field(default_factory=list)
    standards: list[str] = field(default_factory=list)
    confidence: float = 0.0
    recommendation: str = "APPROVE"
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "reasons": self.reasons,
            "standards": self.standards,
            "confidence": self.confidence,
            "recommendation": self.recommendation,
            "summary": self.summary,
        }


@dataclass
class CriticReport:
    issues: list[str] = field(default_factory=list)
    violated_standards: list[str] = field(default_factory=list)
    risk_score: float = 0.0
    recommendation: str = "REVISE"
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "issues": self.issues,
            "violated_standards": self.violated_standards,
            "risk_score": self.risk_score,
            "recommendation": self.recommendation,
            "summary": self.summary,
        }


@dataclass
class MediationResult:
    final_score: float
    advocate_score: float
    critic_score: float
    ontology_issues: list[str]
    domain: str
    weights: dict[str, float]
    advocate_report: AdvocateReport | None = None
    critic_report: CriticReport | None = None


@dataclass
class DecisionResult:
    decision: str
    action: str
    final_score: float
    advocate_score: float
    critic_score: float
    ontology_issues: list[str] = field(default_factory=list)
    audit_trail: dict = field(default_factory=dict)


@dataclass
class PipelineResult:
    result: Any
    decision: DecisionResult | Any
    mode: str
    audit_trail: dict = field(default_factory=dict)
