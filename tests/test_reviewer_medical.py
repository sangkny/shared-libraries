"""ReviewerAgent — MEDI D R2 Day 2 강화 검증 (low-confidence + 비인증 모델 hint).

테스트 철학 (Mock 0):
    - LLM 호출 없이 ``_parse_review`` static method 만 단독 호출
    - ``ValidationResult`` 객체를 직접 구성해 의료 도메인 warning 시나리오 검증
"""
from __future__ import annotations

import pytest

from agents.reviewer import ReviewerAgent
from ontology.base import (
    OntologyDomain,
    Severity,
    ValidationError,
    ValidationResult,
    ValidatorType,
)


def _med_warning(code: str, value=None) -> ValidationError:
    return ValidationError(
        code=code,
        message=f"sim warning {code}",
        severity=Severity.WARNING,
        validator=ValidatorType.SEMANTIC,
        field="confidence" if code == "MED-SEM-004" else "model_used",
        value=value,
    )


def _result_with_warnings(warnings: list[ValidationError]) -> ValidationResult:
    r = ValidationResult(passed=True, domain=OntologyDomain.MEDICAL)
    for w in warnings:
        r.add(w)
    return r


class TestReviewerMedicalEnhancements:
    """D R2 Day 2 — 의료 도메인 자동 hint 변환."""

    def test_low_confidence_warning_becomes_review_hint(self) -> None:
        """MED-SEM-004 warning → "DiagnosisReview 큐 자동 승격 권장" hint."""
        ont = _result_with_warnings([_med_warning("MED-SEM-004", value=0.32)])
        review = ReviewerAgent._parse_review(
            raw="VERDICT: PASS\nFEEDBACK: ok",
            ontology_result=ont,
        )
        assert review.passed is True
        assert any(
            "DiagnosisReview" in h and "승격" in h
            for h in review.improvement_hints
        ), f"hints={review.improvement_hints}"

    def test_unknown_model_warning_becomes_review_hint(self) -> None:
        """MED-SEM-007 warning → "임상 사용 전 모델 검증 필요" hint."""
        ont = _result_with_warnings(
            [_med_warning("MED-SEM-007", value="rando/llm-v0")]
        )
        review = ReviewerAgent._parse_review(
            raw="VERDICT: PASS\nFEEDBACK: ok",
            ontology_result=ont,
        )
        assert any(
            "비인증 모델" in h and "rando/llm-v0" in h
            for h in review.improvement_hints
        ), f"hints={review.improvement_hints}"

    def test_no_med_warnings_no_auto_hint(self) -> None:
        """warning 없으면 자동 hint 도 없음 (LLM 출력 hint 만 유지)."""
        ont = _result_with_warnings([])
        review = ReviewerAgent._parse_review(
            raw="VERDICT: PASS\nFEEDBACK: ok\nIMPROVEMENTS:\n- LLM-suggested-fix",
            ontology_result=ont,
        )
        assert review.improvement_hints == ["LLM-suggested-fix"]

    def test_ontology_error_forces_fail_with_low_confidence_hint(self) -> None:
        """오류 + low-confidence warning 결합 — FAIL 이지만 hint 도 유지."""
        ont = _result_with_warnings([_med_warning("MED-SEM-004", value=0.2)])
        ont.add(
            ValidationError(
                code="MED-STR-001",
                message="missing required",
                severity=Severity.ERROR,
                validator=ValidatorType.STRUCTURAL,
                field="patient_id",
            )
        )
        review = ReviewerAgent._parse_review(
            raw="VERDICT: PASS\nFEEDBACK: ok",
            ontology_result=ont,
        )
        assert review.passed is False
        assert any("DiagnosisReview" in h for h in review.improvement_hints)
