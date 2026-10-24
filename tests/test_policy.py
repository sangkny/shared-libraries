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


def test_medi_clinical_actions_follow_existing_roles() -> None:
    e = PolicyEngine()
    assert e.check("medi-iot", "doctor", "clinical_observation_create")
    assert e.check("medi-iot", "doctor", "clinical_observation_correct")
    assert e.check("medi-iot", "staff", "clinical_observation_read")
    assert not e.check("medi-iot", "staff", "clinical_observation_create")
    assert e.check("medi-iot", "doctor", "read")
    assert e.check("medi-iot", "staff", "create_patient")


def test_medi_query_and_quality_actions() -> None:
    engine = PolicyEngine()
    for action in (
        "clinical_query_read",
        "clinical_query_create",
        "clinical_query_respond",
        "clinical_query_review",
        "clinical_quality_read",
        "clinical_quality_execute",
    ):
        assert engine.check("medi-iot", "doctor", action)
    assert engine.check("medi-iot", "staff", "clinical_query_read")
    assert engine.check("medi-iot", "staff", "clinical_quality_read")
    assert not engine.check("medi-iot", "staff", "clinical_query_create")
    assert not engine.check("medi-iot", "staff", "clinical_quality_execute")


def test_medi_lock_actions_stay_on_existing_roles() -> None:
    engine = PolicyEngine()
    for action in (
        "clinical_lock_read",
        "clinical_lock_request",
        "clinical_lock_apply",
        "clinical_unlock_request",
        "clinical_unlock_approve",
        "clinical_readiness_read",
        "clinical_monitor_execute",
        "clinical_protocol_manage",
    ):
        assert engine.check("medi-iot", "doctor", action)
    assert engine.check("medi-iot", "staff", "clinical_lock_read")
    assert engine.check("medi-iot", "staff", "clinical_readiness_read")
    assert not engine.check("medi-iot", "staff", "clinical_lock_apply")
    assert not engine.check("medi-iot", "staff", "clinical_unlock_approve")
    assert not engine.check("medi-iot", "staff", "clinical_monitor_execute")


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
