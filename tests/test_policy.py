"""PolicyEngine — Phase 2 Week 11."""
from __future__ import annotations

from auth.policy import PolicyEngine, POLICIES


def test_policies_shape() -> None:
    assert "medi-iot" in POLICIES
    assert "doctor" in POLICIES["medi-iot"]


def test_medi_doctor_ai_analyze() -> None:
    e = PolicyEngine()
    assert e.check("medi-iot", "doctor", "ai_analyze")
    assert not e.check("medi-iot", "doctor", "dashboard_stats")


def test_medi_admin_wildcard() -> None:
    e = PolicyEngine()
    assert e.check("medi-iot", "admin", "dashboard_stats")


def test_autonogada_developer_generate() -> None:
    e = PolicyEngine()
    assert e.check("autonogada", "developer", "generate")
    assert e.check("autonogada", "developer", "svg")


def test_coops_roles() -> None:
    e = PolicyEngine()
    assert e.check("coops", "staff", "request_approval")
    assert e.check("coops", "manager", "approve")
    assert not e.check("coops", "staff", "approve")
