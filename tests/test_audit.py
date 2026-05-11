"""감사·PII 징후 — Phase 2 Week 11."""
from __future__ import annotations

from auth.audit import log_with_ontology, reset_pii_counters_for_tests


def test_log_with_ontology_ssn_event_threshold() -> None:
    reset_pii_counters_for_tests()
    for _ in range(3):
        log_with_ontology(
            "u-ssn-test",
            "/t",
            {"code": "x = '123-45-6789'", "passed": False},
            redis_url=None,
        )
    reset_pii_counters_for_tests()


def test_log_with_ontology_plain_ok() -> None:
    log_with_ontology("u1", "/ok", {"passed": True, "summary": ""}, redis_url=None)
