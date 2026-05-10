# shared-libraries/ontology/base.py
"""
Ontology 기반 타입 정의
3개 프로젝트(MEDI-IOT / AutoNoGaDa / CoOps) 공통 사용
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── 도메인 정의 ───────────────────────────────────────────
class OntologyDomain(Enum):
    MEDICAL  = "medical"   # MEDI-IOT  — 의료 데이터, ICD-10, 안과 진단
    SOFTWARE = "software"  # AutoNoGaDa — 코드 품질, 함수명, 복잡도
    BUSINESS = "business"  # CoOps     — 프로세스, 계약, 승인 흐름
    GENERAL  = "general"   # 공통 — 도메인 무관 검증
    # Phase 2 — AutoNoGaDa 확장 (PHASE2_ONTOLOGY_DESIGN.md)
    SVG = "svg"             # SVG 생성 · 시각화 · XSS/용량
    POLYGLOT = "polyglot"   # 다중 언어 코드 일관성
    KNOWLEDGE = "knowledge"  # RAG · 태스크 임베딩 스키마
    COST = "cost"           # LLM 라우팅 · 예산


# ── 검증기 종류 ───────────────────────────────────────────
class ValidatorType(Enum):
    SEMANTIC    = "semantic"    # 의미/개념 검증 (ICD-10 코드가 유효한가?)
    STRUCTURAL  = "structural"  # 구조 검증    (필수 필드가 있는가?)
    CONSTRAINT  = "constraint"  # 제약 검증    (값이 허용 범위 안인가?)
    DEPENDENCY  = "dependency"  # 의존성 검증  (A가 있으면 B도 있어야 하는가?)


# ── 심각도 ────────────────────────────────────────────────
class Severity(Enum):
    ERROR   = "error"    # 반드시 수정 — 검증 실패
    WARNING = "warning"  # 권장 수정  — 검증 통과하지만 경고
    INFO    = "info"     # 참고 정보  — 검증 통과


# ── 검증 오류 단위 ────────────────────────────────────────
@dataclass
class ValidationError:
    """단일 검증 오류"""
    code:        str           # 오류 코드 (예: "MED-001", "SW-003")
    message:     str           # 사람이 읽을 수 있는 오류 메시지
    severity:    Severity      # ERROR | WARNING | INFO
    validator:   ValidatorType # 어떤 Validator에서 발생했는지
    field:       str   = ""    # 오류가 발생한 필드명
    value:       Any   = None  # 오류가 발생한 실제 값
    suggestion:  str   = ""    # 수정 제안

    def __str__(self):
        loc = f"[{self.field}]" if self.field else ""
        return f"[{self.severity.value.upper()}] {self.code} {loc} {self.message}"


# ── 검증 결과 ─────────────────────────────────────────────
@dataclass
class ValidationResult:
    """OntologyValidator 전체 실행 결과"""
    passed:   bool
    domain:   OntologyDomain
    errors:   list[ValidationError] = field(default_factory=list)
    warnings: list[ValidationError] = field(default_factory=list)
    infos:    list[ValidationError] = field(default_factory=list)
    metadata: dict                  = field(default_factory=dict)

    # ── 편의 속성 ──────────────────────────────────────────
    @property
    def error_count(self)   -> int: return len(self.errors)
    @property
    def warning_count(self) -> int: return len(self.warnings)

    @property
    def summary(self) -> str:
        status = "✅ PASS" if self.passed else "❌ FAIL"
        return (
            f"{status} | domain={self.domain.value} | "
            f"errors={self.error_count} warnings={self.warning_count}"
        )

    def add(self, error: ValidationError):
        """오류 추가 — 심각도에 따라 자동 분류"""
        if error.severity == Severity.ERROR:
            self.errors.append(error)
            self.passed = False
        elif error.severity == Severity.WARNING:
            self.warnings.append(error)
        else:
            self.infos.append(error)

    def merge(self, other: "ValidationResult"):
        """다른 ValidationResult 병합"""
        for e in other.errors:   self.add(e)
        for w in other.warnings: self.add(w)
        for i in other.infos:    self.add(i)
        if not other.passed:
            self.passed = False

    def to_dict(self) -> dict:
        return {
            "passed":   self.passed,
            "domain":   self.domain.value,
            "summary":  self.summary,
            "errors":   [{"code": e.code, "message": e.message,
                          "field": e.field, "suggestion": e.suggestion}
                         for e in self.errors],
            "warnings": [{"code": w.code, "message": w.message,
                          "field": w.field}
                         for w in self.warnings],
            "metadata": self.metadata,
        }
