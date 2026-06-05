# shared-libraries/ontology/validator.py
"""
OntologyValidator — 핵심 검증 엔진
4개 서브 Validator + 도메인별 규칙 로더

사용법:
    validator = OntologyValidator(domain=OntologyDomain.MEDICAL)
    result = await validator.validate(patient_data)
    print(result.summary)
"""
import logging
from abc import ABC, abstractmethod
from typing import Any

from .base import (
    OntologyDomain, ValidatorType, Severity,
    ValidationError, ValidationResult,
)

log = logging.getLogger("ontology.validator")


# ════════════════════════════════════════════════════════════
# SECTION 1 — 서브 Validator 추상 기반
# ════════════════════════════════════════════════════════════

class BaseSubValidator(ABC):
    """모든 서브 Validator의 기반 클래스"""

    def __init__(self, domain: OntologyDomain):
        self.domain = domain

    @property
    @abstractmethod
    def validator_type(self) -> ValidatorType:
        ...

    @abstractmethod
    async def validate(self, data: Any, rules: dict) -> ValidationResult:
        """
        데이터 검증 실행
        Args:
            data:  검증할 데이터 (dict, str, list 등)
            rules: 도메인 규칙 (ontology rules dict)
        Returns:
            ValidationResult
        """
        ...

    def _result(self) -> ValidationResult:
        """새 ValidationResult 생성 헬퍼"""
        return ValidationResult(passed=True, domain=self.domain)

    def _error(self, code: str, message: str, field: str = "",
               value: Any = None, suggestion: str = "") -> ValidationError:
        return ValidationError(
            code=code, message=message, severity=Severity.ERROR,
            validator=self.validator_type, field=field,
            value=value, suggestion=suggestion,
        )

    def _warning(self, code: str, message: str, field: str = "",
                 value: Any = None, suggestion: str = "") -> ValidationError:
        return ValidationError(
            code=code, message=message, severity=Severity.WARNING,
            validator=self.validator_type, field=field,
            value=value, suggestion=suggestion,
        )

    def _info(self, code: str, message: str, field: str = "") -> ValidationError:
        return ValidationError(
            code=code, message=message, severity=Severity.INFO,
            validator=self.validator_type, field=field,
        )


# ════════════════════════════════════════════════════════════
# SECTION 2 — 4개 서브 Validator 구현
# ════════════════════════════════════════════════════════════

class SemanticValidator(BaseSubValidator):
    """
    의미/개념 검증
    - MEDICAL:  ICD-10 코드 유효성, 의료 용어 표준화
    - SOFTWARE: 함수/변수명 네이밍 컨벤션, 의미있는 이름
    - BUSINESS: 비즈니스 용어 표준화, 프로세스 명칭
    """

    @property
    def validator_type(self) -> ValidatorType:
        return ValidatorType.SEMANTIC

    async def validate(self, data: Any, rules: dict) -> ValidationResult:
        result = self._result()
        semantic_rules = rules.get("semantic", {})

        if not semantic_rules:
            result.add(self._info("SEM-000", "semantic 규칙이 정의되지 않았습니다."))
            return result

        if self.domain == OntologyDomain.MEDICAL:
            await self._validate_medical_semantic(data, semantic_rules, result)
        elif self.domain == OntologyDomain.SOFTWARE:
            await self._validate_software_semantic(data, semantic_rules, result)
        elif self.domain == OntologyDomain.BUSINESS:
            await self._validate_business_semantic(data, semantic_rules, result)
        elif self.domain == OntologyDomain.SVG:
            await self._validate_svg_semantic(data, semantic_rules, result)
        elif self.domain == OntologyDomain.POLYGLOT:
            await self._validate_polyglot_semantic(data, semantic_rules, result)

        return result

    async def _validate_medical_semantic(self, data: dict, rules: dict,
                                          result: ValidationResult):
        """의료 데이터 의미 검증 (D R2 Day 2 — 안과·임상 강화 룰셋).

        검증 룰 (코드 prefix MED-SEM-*):
            001 — ICD-10 코드 형식
            002 — 의료 용어 화이트리스트
            003 — 안과 ICD-10 카테고리 (H/E11/E08~10) 일치 — eye_condition 이 있을 때
            004 — confidence 0~1 범위
            005 — severity ↔ urgency 일관성 (severe → urgent/emergency)
            006 — laterality (OD/OS/OU) 검증 + finding_side 동기
            007 — model_used 알려진 모델 화이트리스트 (warning)
        """
        # ── 001 — ICD-10 코드 형식 ─────────────────────────────
        icd10_fields = rules.get("icd10_fields", [])
        for field in icd10_fields:
            val = data.get(field, "")
            if val and not self._is_valid_icd10(val):
                result.add(self._error(
                    "MED-SEM-001",
                    f"ICD-10 코드 형식이 올바르지 않습니다: '{val}'",
                    field=field, value=val,
                    suggestion="올바른 형식 예시: H35.0 (황반변성), E11.9 (2형 당뇨)",
                ))

        # ── 002 — 표준 의료 용어 화이트리스트 ────────────────
        terminology_fields = rules.get("terminology_fields", {})
        for field, allowed_terms in terminology_fields.items():
            val = data.get(field, "")
            if val and val not in allowed_terms:
                result.add(self._warning(
                    "MED-SEM-002",
                    f"표준 의료 용어가 아닙니다: '{val}'",
                    field=field, value=val,
                    suggestion=f"허용 용어: {', '.join(allowed_terms[:5])}",
                ))

        # ── 003 — 안과 진단인데 ICD-10 prefix 가 무관한 경우 ─
        allowed_categories = rules.get("allowed_icd10_categories", [])
        if allowed_categories:
            primary = (data.get("diagnosis_code") or data.get("icd10_code") or "")
            condition = (data.get("eye_condition") or "").strip().lower()
            if primary and condition and condition != "normal":
                prefix_ok = any(
                    str(primary).upper().startswith(cat.upper())
                    for cat in allowed_categories
                )
                if not prefix_ok:
                    result.add(self._warning(
                        "MED-SEM-003",
                        f"안과 진단 '{condition}' 에 대해 ICD-10 카테고리가 안과 영역(H/E11)에 속하지 않습니다: '{primary}'",
                        field="diagnosis_code",
                        value=primary,
                        suggestion=f"허용 카테고리 prefix: {', '.join(allowed_categories)}",
                    ))

        # ── 004 — confidence 0~1 ─────────────────────────────
        conf_field = rules.get("confidence_field", "confidence")
        if conf_field in data and data[conf_field] is not None:
            try:
                c = float(data[conf_field])
                if c < 0.0 or c > 1.0:
                    result.add(self._error(
                        "MED-SEM-004",
                        f"confidence 값은 0.0~1.0 범위여야 합니다: {c}",
                        field=conf_field, value=c,
                        suggestion="LLM 출력의 confidence 정규화(0..1)를 확인하세요.",
                    ))
                elif c < 0.5:
                    result.add(self._warning(
                        "MED-SEM-004",
                        f"낮은 confidence ({c:.2f}) — 의사 검토 단계로 자동 승격 권장",
                        field=conf_field, value=c,
                        suggestion="ReviewerAgent: confidence < 0.5 → DiagnosisReview pending 자동 큐",
                    ))
            except (TypeError, ValueError):
                pass

        # ── 005 — severity ↔ urgency 일관성 ─────────────────
        sev_urg = rules.get("severity_urgency_map", {})
        sev = (data.get("severity") or "").strip().lower()
        urg = (data.get("urgency") or "").strip().lower()
        if sev and urg and sev_urg:
            allowed_urg = sev_urg.get(sev, [])
            if allowed_urg and urg not in allowed_urg:
                result.add(self._error(
                    "MED-SEM-005",
                    f"severity '{sev}' 와 urgency '{urg}' 의 임상 매핑이 불일치합니다.",
                    field="urgency", value=urg,
                    suggestion=f"severity={sev} 에 권장되는 urgency: {', '.join(allowed_urg)}",
                ))

        # ── 006 — laterality + finding_side 동기 ────────────
        lat = (data.get("laterality") or "").strip().upper()
        fs = (data.get("finding_side") or "").strip().upper()
        if lat and fs:
            lat_to_sides = {"OD": {"OD", "RIGHT", "R"},
                            "OS": {"OS", "LEFT", "L"},
                            "OU": {"OD", "OS", "OU", "BOTH", "RIGHT", "LEFT", "R", "L"}}
            allowed = lat_to_sides.get(lat, set())
            if allowed and fs not in allowed:
                result.add(self._error(
                    "MED-SEM-006",
                    f"laterality={lat} 인데 finding_side={fs} 는 일관성이 없습니다.",
                    field="finding_side", value=fs,
                    suggestion="laterality 와 finding_side 를 일치시키거나 laterality=OU 로 설정하세요.",
                ))

        # ── 007 — model_used 화이트리스트 (warning) ─────────
        model_whitelist = rules.get("model_whitelist", [])
        mu = data.get("model_used")
        if mu and model_whitelist:
            mlow = str(mu).lower()
            glaucoma_models = rules.get("glaucoma_model_whitelist", [])
            cnn_ok = "cnn(" in mlow and any(
                g.lower() in mlow for g in glaucoma_models
            )
            if not cnn_ok and not any(w.lower() in mlow for w in model_whitelist):
                result.add(self._warning(
                    "MED-SEM-007",
                    f"model_used '{mu}' 가 인증된 의료 모델 화이트리스트에 없습니다.",
                    field="model_used", value=mu,
                    suggestion=f"화이트리스트: {', '.join(model_whitelist)} — 임상 사용 전 모델 검증을 권장합니다.",
                ))

        # ── Glaucoma CNN API (task=glaucoma) ───────────────
        if data.get("task") == "glaucoma" or data.get("glaucoma_grade") is not None:
            await self._validate_glaucoma_semantic(
                data, rules.get("glaucoma_semantic", {}), result
            )

    async def _validate_glaucoma_semantic(
        self, data: dict, rules: dict, result: ValidationResult
    ) -> None:
        """녹내장 CNN 결과 의미 검증 (GLAU-SEM-001~004)."""
        icd = str(data.get("icd10_code") or data.get("icd10") or "").strip().upper()
        grade = data.get("glaucoma_grade")
        if icd:
            if not self._is_valid_icd10(icd):
                result.add(self._error(
                    "GLAU-SEM-001",
                    f"ICD-10 코드 형식이 올바르지 않습니다: '{icd}'",
                    field="icd10_code", value=icd,
                    suggestion="녹내장: H40.0(정상)~H40.9",
                ))
            elif grade is not None and int(grade) >= 1:
                if not icd.startswith("H40"):
                    result.add(self._error(
                        "GLAU-SEM-001",
                        f"glaucoma_grade≥1 인데 ICD-10이 H40.x 가 아닙니다: '{icd}'",
                        field="icd10_code", value=icd,
                        suggestion="H40.1 (개방각 녹내장) 등 H40 계열 사용",
                    ))
            elif grade is not None and int(grade) == 0:
                if icd.startswith("H40") and icd not in ("H40.0",):
                    result.add(self._warning(
                        "GLAU-SEM-001",
                        f"정상 등급인데 ICD-10이 H40.0이 아닙니다: '{icd}'",
                        field="icd10_code", value=icd,
                        suggestion="정상: H40.0 또는 빈 값",
                    ))

        try:
            prob = float(data.get("probability", -1))
        except (TypeError, ValueError):
            prob = -1.0
        risk = str(data.get("risk_level") or "").upper()
        if prob >= 0.5 and risk not in ("MODERATE", "HIGH"):
            result.add(self._error(
                "GLAU-SEM-002",
                f"probability={prob:.2f}≥0.5 인데 risk_level이 MODERATE/HIGH가 아닙니다: '{risk}'",
                field="risk_level", value=risk,
                suggestion="probability≥0.5 → MODERATE 또는 HIGH",
            ))
        if 0 <= prob < 0.3 and risk != "LOW":
            result.add(self._warning(
                "GLAU-SEM-002",
                f"probability={prob:.2f}<0.3 인데 risk_level='{risk}'",
                field="risk_level", value=risk,
            ))

        grade_label = str(data.get("grade_label") or data.get("severity") or "").lower()
        expected = {0: "normal", 1: "suspect", 2: "glaucoma"}.get(int(grade) if grade is not None else -1)
        if expected and grade_label and grade_label != expected:
            result.add(self._error(
                "GLAU-SEM-003",
                f"glaucoma_grade={grade} 와 grade_label='{grade_label}' 불일치 (기대: {expected})",
                field="grade_label", value=grade_label,
            ))

        referral_map = rules.get(
            "referral_risk_map",
            {"HIGH": "immediate", "MODERATE": "routine", "LOW": "none"},
        )
        referral = str(data.get("referral_urgency") or "").lower()
        if risk and referral:
            expected_ref = referral_map.get(risk, "")
            if expected_ref and referral != expected_ref:
                result.add(self._error(
                    "GLAU-SEM-004",
                    f"risk_level={risk} 인데 referral_urgency='{referral}' (기대: {expected_ref})",
                    field="referral_urgency", value=referral,
                ))

        cdr_raw = data.get("cup_disc_ratio")
        cdr_val = -1.0
        if isinstance(cdr_raw, dict):
            try:
                cdr_val = float(cdr_raw.get("value", -1))
            except (TypeError, ValueError):
                cdr_val = -1.0
        elif cdr_raw is not None:
            try:
                cdr_val = float(cdr_raw)
            except (TypeError, ValueError):
                cdr_val = -1.0

        if cdr_val > 0.75 and risk != "HIGH":
            result.add(self._error(
                "GLAU-SEM-005",
                f"CDR={cdr_val:.2f}>0.75 인데 risk_level이 HIGH가 아닙니다: '{risk}'",
                field="risk_level", value=risk,
                suggestion="CDR>0.75 → risk_level=HIGH",
            ))
        elif 0.65 <= cdr_val <= 0.75 and risk not in ("MODERATE", "HIGH"):
            result.add(self._error(
                "GLAU-SEM-005",
                f"CDR={cdr_val:.2f} (0.65~0.75) 인데 risk_level이 MODERATE/HIGH가 아닙니다: '{risk}'",
                field="risk_level", value=risk,
                suggestion="CDR 0.65~0.75 → risk_level MODERATE 이상",
            ))
        elif 0 <= cdr_val < 0.65 and risk == "HIGH":
            result.add(self._warning(
                "GLAU-SEM-005",
                f"CDR={cdr_val:.2f}<0.65 인데 risk_level=HIGH",
                field="risk_level", value=risk,
            ))

    async def _validate_software_semantic(self, data: dict, rules: dict,
                                           result: ValidationResult):
        """소프트웨어 코드 의미 검증"""
        # 함수명 네이밍 컨벤션 (snake_case)
        naming_fields = rules.get("naming_fields", [])
        for field in naming_fields:
            val = data.get(field, "")
            if val and not self._is_snake_case(val):
                result.add(self._warning(
                    "SW-SEM-001",
                    f"함수/변수명이 snake_case 규칙에 맞지 않습니다: '{val}'",
                    field=field, value=val,
                    suggestion=f"권장: '{self._to_snake_case(val)}'"
                ))

        # 금지어 검증 (너무 짧거나 의미없는 이름)
        forbidden_names = rules.get("forbidden_names",
                                     ["x", "y", "z", "a", "b", "tmp", "temp", "foo", "bar"])
        name_fields = rules.get("naming_fields", [])
        for field in name_fields:
            val = data.get(field, "")
            if val and val.lower() in forbidden_names:
                result.add(self._error(
                    "SW-SEM-002",
                    f"의미없는 변수명입니다: '{val}'",
                    field=field, value=val,
                    suggestion="변수의 목적을 명확히 나타내는 이름을 사용하세요."
                ))

    async def _validate_business_semantic(self, data: dict, rules: dict,
                                           result: ValidationResult):
        """비즈니스 프로세스 의미 검증"""
        # 프로세스 상태 유효성
        status_fields = rules.get("status_fields", {})
        for field, allowed_statuses in status_fields.items():
            val = data.get(field, "")
            if val and val not in allowed_statuses:
                result.add(self._error(
                    "BIZ-SEM-001",
                    f"유효하지 않은 프로세스 상태: '{val}'",
                    field=field, value=val,
                    suggestion=f"허용 상태: {', '.join(allowed_statuses)}"
                ))

    async def _validate_svg_semantic(self, data: dict, rules: dict,
                                     result: ValidationResult) -> None:
        """SVG XML — XSS·외부 URL·요소 수·medical_report PII"""
        import re

        if not isinstance(data, dict):
            return

        field = rules.get("svg_content_field", "svg_content")
        raw = data.get(field, "")
        content = raw if isinstance(raw, str) else str(raw)
        lower = content.lower()

        if "<script" in lower or "javascript:" in lower:
            result.add(self._error(
                "SVG-SEM-XSS",
                "<script> 또는 javascript: URL은 허용되지 않습니다 (XSS 방지).",
                field=field,
                suggestion="script 태그를 제거하고 정적 SVG만 사용하세요.",
            ))

        if re.search(r'(?:xlink:href|href)\s*=\s*["\']?\s*https?://', content, re.I):
            result.add(self._error(
                "SVG-SEM-URL",
                "SVG에서 외부 http(s) URL 참조는 허용되지 않습니다.",
                field=field,
                suggestion="외부 리소스 대신 인라인 path/symbol을 사용하세요.",
            ))

        open_tags = len(re.findall(r"<[^/!?][^>]*>", content))
        max_el = int(rules.get("svg_max_elements", 1000))
        if open_tags > max_el:
            result.add(self._error(
                "SVG-SEM-EL",
                f"SVG 요소(태그) 수가 한도를 초과했습니다: {open_tags} (최대 {max_el})",
                field=field,
                value=open_tags,
            ))

        svg_type = data.get("svg_type", "")
        if svg_type == "medical_report" and content:
            if re.search(
                r"\b\d{6}-[1-4]\d{6}\b",
                content,
            ) or re.search(r"\b01[016789]-\d{3,4}-\d{4}\b", content):
                result.add(self._error(
                    "SVG-SEM-PII",
                    "medical_report SVG에 주민번호·전화번호 형태의 PII 패턴이 감지되었습니다.",
                    field=field,
                    suggestion="PII를 제거하거나 마스킹하세요.",
                ))
            pii_kw = ("주민", "resident", "ssn", "환자명", "환자 이름")
            if any(k.lower() in lower for k in pii_kw):
                result.add(self._warning(
                    "SVG-SEM-PII-HINT",
                    "medical_report에 PII로 해석될 수 있는 키워드가 포함되어 있습니다.",
                    field=field,
                ))

    async def _validate_polyglot_semantic(self, data: dict, rules: dict,
                                          result: ValidationResult) -> None:
        """다중 언어 코드 스니펫 검증"""
        import re

        if not isinstance(data, dict):
            return

        lang = str(rules.get("polyglot_language", "python")).lower().strip()
        code_field = rules.get("code_field", "code")
        raw = data.get(code_field, "")
        code = raw if isinstance(raw, str) else str(raw)
        if not code.strip():
            return

        if lang == "python":
            if re.search(r"\beval\s*\(", code) or re.search(r"\bexec\s*\(", code):
                result.add(self._error(
                    "POLY-SEM-001",
                    "Python 코드에서 eval()/exec() 사용은 금지됩니다.",
                    field=code_field,
                ))
            if re.search(r"os\.system\s*\(", code):
                result.add(self._error(
                    "POLY-SEM-002",
                    "os.system() 호출은 금지됩니다.",
                    field=code_field,
                ))
            fn = data.get("function_name")
            if isinstance(fn, str) and fn and not self._is_snake_case(fn):
                result.add(self._warning(
                    "POLY-SEM-003",
                    f"Python 함수명은 snake_case를 권장합니다: '{fn}'",
                    field="function_name", value=fn,
                ))
            if "->" not in code and not re.search(
                r":\s*(?:int|str|float|bool|list|dict|tuple|Optional|None)",
                code,
            ):
                result.add(self._warning(
                    "POLY-SEM-004",
                    "타입 힌트(매개변수 또는 -> 반환) 포함을 권장합니다.",
                    field=code_field,
                ))

        elif lang == "typescript":
            fn = data.get("function_name")
            if isinstance(fn, str) and fn:
                if not re.match(r"^[a-z][a-zA-Z0-9]*$", fn):
                    result.add(self._warning(
                        "POLY-SEM-010",
                        "TypeScript 함수명은 camelCase를 권장합니다.",
                        field="function_name", value=fn,
                    ))
            if re.search(r":\s*any\b", code) or re.search(r"\bas\s+any\b", code):
                result.add(self._error(
                    "POLY-SEM-011",
                    "TypeScript 코드에서 any 타입 사용은 금지입니다.",
                    field=code_field,
                ))

        elif lang == "rust":
            fn = data.get("function_name")
            if isinstance(fn, str) and fn and not self._is_snake_case(fn):
                result.add(self._warning(
                    "POLY-SEM-020",
                    f"Rust 함수명은 snake_case를 권장합니다: '{fn}'",
                    field="function_name", value=fn,
                ))
            unwrap_n = len(re.findall(r"\.unwrap\s*\(\s*\)", code))
            if unwrap_n > 3:
                result.add(self._error(
                    "POLY-SEM-021",
                    f"unwrap()이 함수당 최대 3개까지 허용됩니다 (현재 {unwrap_n}개).",
                    field=code_field,
                    value=unwrap_n,
                ))

    @staticmethod
    def _is_valid_icd10(code: str) -> bool:
        """ICD-10 코드 형식 검증: 영문자 + 숫자 + 선택적 소수점"""
        import re
        return bool(re.match(r'^[A-Z][0-9]{2}(\.[0-9A-Z]{1,4})?$', code.upper()))

    @staticmethod
    def _is_snake_case(name: str) -> bool:
        import re
        return bool(re.match(r'^[a-z][a-z0-9_]*$', name))

    @staticmethod
    def _to_snake_case(name: str) -> str:
        import re
        s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
        return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()


class StructuralValidator(BaseSubValidator):
    """
    구조 검증 — 필수 필드, 타입, 형식
    - MEDICAL:  환자ID, 진단코드, 날짜 등 필수 필드
    - SOFTWARE: 함수 시그니처, docstring, 반환타입
    - BUSINESS: 계약번호, 담당자, 승인일자
    """

    @property
    def validator_type(self) -> ValidatorType:
        return ValidatorType.STRUCTURAL

    async def validate(self, data: Any, rules: dict) -> ValidationResult:
        result = self._result()
        structural_rules = rules.get("structural", {})

        if not structural_rules:
            result.add(self._info("STR-000", "structural 규칙이 정의되지 않았습니다."))
            return result

        # ── 필수 필드 검증 ──────────────────────────────────
        required_fields = structural_rules.get("required_fields", [])
        for field in required_fields:
            val = self._get_nested(data, field)
            if val is None or val == "" or val == []:
                result.add(self._error(
                    f"{self._domain_prefix()}-STR-001",
                    f"필수 필드가 없거나 비어있습니다: '{field}'",
                    field=field,
                    suggestion=f"'{field}' 필드를 반드시 입력하세요."
                ))

        # ── 타입 검증 ──────────────────────────────────────
        type_rules = structural_rules.get("field_types", {})
        for field, expected_type in type_rules.items():
            val = self._get_nested(data, field)
            if val is not None and not self._check_type(val, expected_type):
                result.add(self._error(
                    f"{self._domain_prefix()}-STR-002",
                    f"필드 타입이 올바르지 않습니다: '{field}' "
                    f"(기대: {expected_type}, 실제: {type(val).__name__})",
                    field=field, value=val,
                    suggestion=f"'{field}'은 {expected_type} 타입이어야 합니다."
                ))

        # ── 형식 검증 (정규식) ─────────────────────────────
        format_rules = structural_rules.get("field_formats", {})
        for field, pattern in format_rules.items():
            import re
            val = str(self._get_nested(data, field) or "")
            if val and not re.match(pattern, val):
                result.add(self._error(
                    f"{self._domain_prefix()}-STR-003",
                    f"필드 형식이 올바르지 않습니다: '{field}' = '{val}'",
                    field=field, value=val,
                    suggestion=f"'{field}'의 형식 패턴: {pattern}"
                ))

        # ── 금지 필드 (보안/개인정보) ──────────────────────
        forbidden_fields = structural_rules.get("forbidden_fields", [])
        for field in forbidden_fields:
            if self._get_nested(data, field) is not None:
                result.add(self._error(
                    f"{self._domain_prefix()}-STR-004",
                    f"포함되어서는 안 되는 필드입니다: '{field}'",
                    field=field,
                    suggestion=f"'{field}' 필드를 제거하거나 마스킹하세요."
                ))

        return result

    def _domain_prefix(self) -> str:
        return {
            "medical": "MED",
            "software": "SW",
            "business": "BIZ",
            "general": "GEN",
            "svg": "SVG",
            "polyglot": "POLY",
            "knowledge": "KNOW",
            "cost": "COST",
        }.get(self.domain.value, "GEN")

    @staticmethod
    def _get_nested(data: Any, field: str) -> Any:
        """중첩 필드 접근 (예: 'patient.name' → data['patient']['name'])"""
        if not isinstance(data, dict):
            return None
        keys = field.split(".")
        val = data
        for key in keys:
            if not isinstance(val, dict):
                return None
            val = val.get(key)
        return val

    @staticmethod
    def _check_type(value: Any, expected: str) -> bool:
        type_map = {
            "str": str, "string": str,
            "int": int, "integer": int,
            "float": float, "number": (int, float),
            "bool": bool, "boolean": bool,
            "list": list, "array": list,
            "dict": dict, "object": dict,
        }
        t = type_map.get(expected.lower())
        return isinstance(value, t) if t else True


class ConstraintValidator(BaseSubValidator):
    """
    제약 검증 — 값 범위, 길이, 허용값
    - MEDICAL:  나이(0-150), 혈압 범위, 시력 범위
    - SOFTWARE: 함수 길이, 복잡도, 파라미터 수
    - BUSINESS: 금액 범위, 날짜 유효성, 승인 레벨
    """

    @property
    def validator_type(self) -> ValidatorType:
        return ValidatorType.CONSTRAINT

    async def validate(self, data: Any, rules: dict) -> ValidationResult:
        result = self._result()
        constraint_rules = rules.get("constraints", {})

        if not constraint_rules:
            result.add(self._info("CON-000", "constraint 규칙이 정의되지 않았습니다."))
            return result

        # ── 숫자 범위 검증 ─────────────────────────────────
        range_rules = constraint_rules.get("ranges", {})
        for field, range_def in range_rules.items():
            val = self._get_val(data, field)
            if val is None:
                continue
            try:
                num = float(val)
                min_v = range_def.get("min")
                max_v = range_def.get("max")
                if min_v is not None and num < min_v:
                    result.add(self._error(
                        "CON-001",
                        f"값이 최솟값보다 작습니다: {field}={num} (최솟값: {min_v})",
                        field=field, value=num,
                        suggestion=f"{field}의 값은 {min_v} 이상이어야 합니다."
                    ))
                if max_v is not None and num > max_v:
                    result.add(self._error(
                        "CON-002",
                        f"값이 최댓값보다 큽니다: {field}={num} (최댓값: {max_v})",
                        field=field, value=num,
                        suggestion=f"{field}의 값은 {max_v} 이하여야 합니다."
                    ))
            except (TypeError, ValueError):
                pass

        # ── 문자열 길이 검증 ───────────────────────────────
        length_rules = constraint_rules.get("lengths", {})
        for field, length_def in length_rules.items():
            val = str(self._get_val(data, field) or "")
            if not val:
                continue
            min_len = length_def.get("min", 0)
            max_len = length_def.get("max", 99999)
            if len(val) < min_len:
                result.add(self._error(
                    "CON-003",
                    f"문자열이 너무 짧습니다: {field} (길이: {len(val)}, 최소: {min_len})",
                    field=field, value=val,
                    suggestion=f"{field}은 최소 {min_len}자 이상이어야 합니다."
                ))
            if len(val) > max_len:
                result.add(self._error(
                    "CON-004",
                    f"문자열이 너무 깁니다: {field} (길이: {len(val)}, 최대: {max_len})",
                    field=field, value=val[:50] + "...",
                    suggestion=f"{field}은 최대 {max_len}자 이하여야 합니다."
                ))

        # ── 허용값 목록 검증 ───────────────────────────────
        enum_rules = constraint_rules.get("enums", {})
        for field, allowed in enum_rules.items():
            val = self._get_val(data, field)
            if val is not None and val not in allowed:
                result.add(self._error(
                    "CON-005",
                    f"허용되지 않은 값입니다: {field}='{val}'",
                    field=field, value=val,
                    suggestion=f"허용 값: {', '.join(str(a) for a in allowed[:10])}"
                ))

        # ── 날짜 유효성 검증 ───────────────────────────────
        date_fields = constraint_rules.get("date_fields", [])
        for field in date_fields:
            val = self._get_val(data, field)
            if val and not self._is_valid_date(str(val)):
                result.add(self._error(
                    "CON-006",
                    f"날짜 형식이 올바르지 않습니다: {field}='{val}'",
                    field=field, value=val,
                    suggestion="올바른 형식: YYYY-MM-DD (예: 2026-05-08)"
                ))

        byte_rules = constraint_rules.get("byte_lengths", {})
        for field, spec in byte_rules.items():
            val = self._get_val(data, field)
            if val is None:
                continue
            if not isinstance(val, str):
                continue
            b_len = len(val.encode("utf-8"))
            max_b = spec.get("max")
            min_b = spec.get("min")
            if min_b is not None and b_len < min_b:
                result.add(self._error(
                    "CON-007",
                    f"바이트 길이가 너무 짧습니다: {field} ({b_len}B, 최소 {min_b}B)",
                    field=field, value=b_len,
                ))
            if max_b is not None and b_len > max_b:
                result.add(self._error(
                    "CON-008",
                    f"바이트 길이가 한도를 초과했습니다: {field} ({b_len}B, 최대 {max_b}B)",
                    field=field, value=b_len,
                    suggestion="SVG/문서 크기를 줄이세요.",
                ))

        list_len_rules = constraint_rules.get("list_lengths", {})
        for field, spec in list_len_rules.items():
            val = self._get_val(data, field)
            if val is None:
                continue
            if not isinstance(val, list):
                continue
            expect = spec.get("len") or spec.get("length")
            if expect is not None and len(val) != int(expect):
                result.add(self._error(
                    "CON-009",
                    f"리스트 길이가 올바르지 않습니다: {field} (기대 {expect}, 실제 {len(val)})",
                    field=field, value=len(val),
                ))

        return result

    @staticmethod
    def _get_val(data: Any, field: str) -> Any:
        if not isinstance(data, dict):
            return None
        keys = field.split(".")
        val = data
        for key in keys:
            if not isinstance(val, dict):
                return None
            val = val.get(key)
        return val

    @staticmethod
    def _is_valid_date(date_str: str) -> bool:
        import re
        from datetime import datetime
        if not re.match(r'^\d{4}-\d{2}-\d{2}', date_str[:10]):
            return False
        try:
            datetime.strptime(date_str[:10], "%Y-%m-%d")
            return True
        except ValueError:
            return False


class DependencyValidator(BaseSubValidator):
    """
    의존성 검증 — A가 있으면 B도 있어야 함
    - MEDICAL:  당뇨 진단 시 → 혈당 수치 필수
                수술 기록 시 → 마취 기록 필수
    - SOFTWARE: async 함수 → await 사용 여부
                import A    → A 실제 사용 여부
    - BUSINESS: 계약 승인 → 결재자 서명 필수
                환불 요청 → 원래 주문번호 필수
    """

    @property
    def validator_type(self) -> ValidatorType:
        return ValidatorType.DEPENDENCY

    async def validate(self, data: Any, rules: dict) -> ValidationResult:
        result = self._result()
        dep_rules = rules.get("dependencies", {})

        if not dep_rules:
            result.add(self._info("DEP-000", "dependency 규칙이 정의되지 않았습니다."))
            return result

        # ── if A exists → B must exist ─────────────────────
        requires_rules = dep_rules.get("requires", [])
        for rule in requires_rules:
            trigger_field = rule.get("if_field")
            trigger_value = rule.get("has_value")       # None이면 존재만 확인
            required_field = rule.get("then_require")
            message = rule.get("message", "")

            trigger_val = self._get_val(data, trigger_field)

            # 트리거 조건 확인
            triggered = False
            if trigger_value is None:
                triggered = trigger_val is not None and trigger_val != ""
            else:
                triggered = trigger_val == trigger_value

            if triggered:
                required_val = self._get_val(data, required_field)
                truthy = rule.get("then_require_truthy", False)
                if truthy:
                    if required_val is not True:
                        result.add(self._error(
                            "DEP-001",
                            message or f"'{required_field}'은(는) True여야 합니다.",
                            field=required_field,
                            suggestion=f"'{required_field}'를 True로 설정하세요.",
                        ))
                elif required_val is None or required_val == "":
                    result.add(self._error(
                        "DEP-001",
                        message or (
                            f"'{trigger_field}'이 설정되면 "
                            f"'{required_field}'도 필수입니다."
                        ),
                        field=required_field,
                        suggestion=f"'{required_field}' 필드를 입력하세요.",
                    ))

        # ── if A exists → B must NOT exist ────────────────
        excludes_rules = dep_rules.get("excludes", [])
        for rule in excludes_rules:
            field_a = rule.get("if_field")
            field_b = rule.get("then_exclude")
            message = rule.get("message", "")

            val_a = self._get_val(data, field_a)
            val_b = self._get_val(data, field_b)

            if (val_a is not None and val_a != "") and \
               (val_b is not None and val_b != ""):
                result.add(self._error(
                    "DEP-002",
                    message or f"'{field_a}'와 '{field_b}'는 동시에 존재할 수 없습니다.",
                    field=field_b,
                    suggestion=f"'{field_a}' 또는 '{field_b}' 중 하나를 제거하세요."
                ))

        # ── 순서 검증 (A → B → C 순서) ────────────────────
        sequence_rules = dep_rules.get("sequences", [])
        for rule in sequence_rules:
            fields   = rule.get("fields", [])
            message  = rule.get("message", "")
            present  = [f for f in fields
                        if self._get_val(data, f) not in (None, "")]
            if present:
                expected_order = [f for f in fields if f in present]
                if present != expected_order:
                    result.add(self._error(
                        "DEP-003",
                        message or f"필드 순서가 올바르지 않습니다: {present}",
                        suggestion=f"올바른 순서: {expected_order}"
                    ))

        cost_pol = dep_rules.get("cost_policy")
        if isinstance(cost_pol, dict) and cost_pol.get("enabled") and isinstance(data, dict):
            self._validate_cost_policy(data, cost_pol, result)

        return result

    def _validate_cost_policy(
        self,
        data: dict,
        policy: dict,
        result: ValidationResult,
    ) -> None:
        """complexity=critical → HEAVY/CONSENSUS, 극소 예산 → local만"""
        cmpl_f = policy.get("complexity_field", "complexity")
        model_f = policy.get("model_field", "selected_model")
        budget_f = policy.get("budget_field", "budget_usd")
        micro = float(policy.get("micro_budget_ceiling", 0.01))

        model_val = data.get(model_f, "")
        mod_str = str(model_val) if model_val is not None else ""

        cmpl = str(data.get(cmpl_f, "")).lower().strip()
        if cmpl == "critical":
            markers = tuple(m.upper() for m in policy.get("heavy_markers", ("HEAVY", "CONSENSUS")))
            if not any(m in mod_str.upper() for m in markers):
                result.add(self._error(
                    "COST-DEP-001",
                    "complexity=critical 인 작업은 HEAVY 또는 CONSENSUS 라우팅 모델이어야 합니다.",
                    field=model_f, value=model_val,
                    suggestion="selected_model에 HEAVY 또는 CONSENSUS 식별자를 포함하세요.",
                ))

        bud = data.get(budget_f)
        try:
            budget_ok = bud is not None and float(bud) < micro
        except (TypeError, ValueError):
            budget_ok = False

        local_hints = tuple(
            h.lower() for h in policy.get(
                "local_hints",
                ("local", "lm-studio", "gemma-4-e4b"),
            )
        )
        if budget_ok:
            mlow = mod_str.lower()
            if not any(h in mlow for h in local_hints):
                result.add(self._error(
                    "COST-DEP-002",
                    f"budget_usd < {micro} 일 때는 로컬(low-cost) 모델만 허용됩니다.",
                    field=model_f, value=model_val,
                    suggestion="LOCAL_FAST 또는 로컬 모델 id를 선택하세요.",
                ))

    @staticmethod
    def _get_val(data: Any, field: str) -> Any:
        if not isinstance(data, dict) or not field:
            return None
        keys = field.split(".")
        val = data
        for key in keys:
            if not isinstance(val, dict):
                return None
            val = val.get(key)
        return val


# ════════════════════════════════════════════════════════════
# SECTION 3 — OntologyValidator (통합 엔진)
# ════════════════════════════════════════════════════════════

class OntologyValidator:
    """
    Ontology 기반 통합 검증 엔진

    4개 서브 Validator를 조합하여 데이터의 의미/구조/제약/의존성을 검증합니다.
    도메인 규칙은 YAML 또는 dict로 주입합니다.

    사용법:
        # 기본 사용 (규칙 없이)
        validator = OntologyValidator(domain=OntologyDomain.MEDICAL)
        result = await validator.validate(patient_data)

        # 규칙 주입
        validator = OntologyValidator(
            domain=OntologyDomain.MEDICAL,
            rules=MEDICAL_RULES
        )
        result = await validator.validate(patient_data)

        # ReviewerAgent에서 사용
        validator = OntologyValidator.for_domain("medical")
        result = await validator.validate(agent_output)
        if result.passed:
            approve()
        else:
            send_to_fixer(result.errors)
    """

    def __init__(
        self,
        domain: OntologyDomain = OntologyDomain.GENERAL,
        rules:  dict | None    = None,
    ):
        self.domain = domain
        self.rules  = rules or self._load_default_rules(domain)

        # 4개 서브 Validator 초기화
        self._validators: list[BaseSubValidator] = [
            SemanticValidator(domain),
            StructuralValidator(domain),
            ConstraintValidator(domain),
            DependencyValidator(domain),
        ]
        log.info(f"OntologyValidator 초기화 — domain={domain.value}")

    # ── 공개 API ─────────────────────────────────────────

    async def validate(self, data: Any) -> ValidationResult:
        """
        전체 검증 실행 (4개 Validator 순서대로)
        하나라도 ERROR 발생 시 passed=False
        """
        final = ValidationResult(passed=True, domain=self.domain)
        final.metadata["validator_count"] = len(self._validators)

        for v in self._validators:
            try:
                sub_result = await v.validate(data, self.rules)
                final.merge(sub_result)
                log.debug(
                    f"[{v.validator_type.value}] "
                    f"errors={sub_result.error_count} "
                    f"warnings={sub_result.warning_count}"
                )
            except Exception as e:
                log.error(f"Validator 실행 오류 [{v.validator_type.value}]: {e}")
                final.add(ValidationError(
                    code="VAL-ERR",
                    message=f"Validator 내부 오류: {e}",
                    severity=Severity.ERROR,
                    validator=v.validator_type,
                ))

        log.info(f"검증 완료 — {final.summary}")
        return final

    async def validate_partial(
        self,
        data: Any,
        validator_types: list[ValidatorType],
    ) -> ValidationResult:
        """특정 Validator만 선택적으로 실행"""
        final = ValidationResult(passed=True, domain=self.domain)
        for v in self._validators:
            if v.validator_type in validator_types:
                sub = await v.validate(data, self.rules)
                final.merge(sub)
        return final

    def update_rules(self, rules: dict):
        """런타임에 규칙 업데이트"""
        self.rules.update(rules)
        log.info(f"규칙 업데이트 완료 — domain={self.domain.value}")

    # ── 4-에이전트 중재 (신규 — 기존 validate API 유지) ─────

    DOMAIN_WEIGHTS = {
        "medical":   {"advocate": 0.40, "critic": 0.60},
        "software":  {"advocate": 0.50, "critic": 0.50},
        "business":  {"advocate": 0.55, "critic": 0.45},
        "iot":       {"advocate": 0.40, "critic": 0.60},
        "iot_device": {"advocate": 0.40, "critic": 0.60},
        "knowledge": {"advocate": 0.50, "critic": 0.50},
    }

    @staticmethod
    def _normalize_mediation_domain(domain: str) -> str:
        d = (domain or "software").strip().lower()
        if d in ("iot_device", "health_data"):
            return "iot"
        return d

    def mediate(self, advocate, critic, domain: str, result: Any = None):
        """찬성/반대 보고서 중재 → final_score 산출"""
        from agents.four_agent_types import MediationResult

        dom = self._normalize_mediation_domain(domain)
        w = dict(self.DOMAIN_WEIGHTS.get(dom, {"advocate": 0.5, "critic": 0.5}))
        if isinstance(result, dict) and result.get("task") == "glaucoma":
            w = {"advocate": 0.5, "critic": 0.5}

        advocate_score = float(getattr(advocate, "confidence", 0.0) or 0.0)
        risk_raw = float(getattr(critic, "risk_score", 0.5) or 0.5)
        if risk_raw >= 0.95:
            risk_raw = 0.5
        critic_score = 1.0 - risk_raw
        if isinstance(result, dict) and result.get("task") == "glaucoma":
            if critic_score < 0.5:
                critic_score = max(0.5, advocate_score * 0.8)
        final_score = advocate_score * w["advocate"] + critic_score * w["critic"]

        ontology_issues = self._check_existing_rules(dom, result, advocate, critic)
        if isinstance(result, dict) and result.get("task") == "glaucoma":
            ontology_issues = [
                i for i in ontology_issues
                if str(i).lower() not in ("consistency", "pii_detected")
            ]
        if ontology_issues:
            final_score *= 0.5

        return MediationResult(
            final_score=round(final_score, 4),
            advocate_score=advocate_score,
            critic_score=critic_score,
            ontology_issues=ontology_issues,
            domain=dom,
            weights=w,
            advocate_report=advocate,
            critic_report=critic,
        )

    def _check_existing_rules(
        self,
        domain: str,
        result: Any = None,
        advocate=None,
        critic=None,
    ) -> list[str]:
        """경량 ontology 신호 — four-agent 중재 시 점수 보정용"""
        issues: list[str] = []
        text = str(result or "").lower()
        if domain == "medical":
            if any(k in text for k in ("pii", "주민", "ssn", "resident_registration")):
                issues.append("PII_DETECTED")
        if domain == "iot":
            compact = text.replace(" ", "")
            if "iop=25" in compact or ("iop" in text and "25" in text):
                issues.append("IOP_OUT_OF_RANGE")
        if critic is not None:
            risk = float(getattr(critic, "risk_score", 0) or 0)
            for std in getattr(critic, "violated_standards", []) or []:
                if not std or std in issues:
                    continue
                # software 메타 스펙: LLM이 PEP8 등을 hallucinate 해도 낮은 risk면 중재 페널티 제외
                if domain == "software" and risk < 0.85:
                    if isinstance(result, dict) and result.get("function_name"):
                        continue
                issues.append(str(std))
        return issues

    @classmethod
    def for_iot_device(cls) -> "OntologyValidator":
        """IoT 기기 도메인 검증기 (four-agent weights: iot)"""
        return cls.for_domain("iot_device")

    # ── 팩토리 메서드 ─────────────────────────────────────

    @classmethod
    def for_domain(cls, domain: str) -> "OntologyValidator":
        """문자열로 도메인 지정"""
        return cls(domain=OntologyDomain(domain.lower()))

    @classmethod
    def for_medical(cls) -> "OntologyValidator":
        """MEDI-IOT용 검증기"""
        return cls(domain=OntologyDomain.MEDICAL,
                   rules=cls._load_default_rules(OntologyDomain.MEDICAL))

    @classmethod
    def for_software(cls) -> "OntologyValidator":
        """AutoNoGaDa용 검증기"""
        return cls(domain=OntologyDomain.SOFTWARE,
                   rules=cls._load_default_rules(OntologyDomain.SOFTWARE))

    @classmethod
    def for_business(cls) -> "OntologyValidator":
        """CoOps용 검증기"""
        return cls(domain=OntologyDomain.BUSINESS,
                   rules=cls._load_default_rules(OntologyDomain.BUSINESS))

    @classmethod
    def for_svg(cls) -> "OntologyValidator":
        """SVG Generator — XSS/용량/PII (medical_report)"""
        return cls(domain=OntologyDomain.SVG,
                   rules=cls._default_rules_svg())

    @classmethod
    def for_polyglot(cls, language: str) -> "OntologyValidator":
        """다중 언어 코드 스니펫 — language: python | typescript | rust"""
        lang = language.strip().lower()
        allowed = frozenset({"python", "typescript", "rust"})
        if lang not in allowed:
            raise ValueError(f"unsupported polyglot language: {language!r} (expected {sorted(allowed)})")
        return cls(domain=OntologyDomain.POLYGLOT,
                   rules=cls._default_rules_polyglot(lang))

    @classmethod
    def for_knowledge(cls) -> "OntologyValidator":
        """RAG / 지식 인덱싱 메타데이터"""
        return cls(domain=OntologyDomain.KNOWLEDGE,
                   rules=cls._default_rules_knowledge())

    @classmethod
    def for_cost(cls) -> "OntologyValidator":
        """비용·모델 라우팅"""
        return cls(domain=OntologyDomain.COST,
                   rules=cls._default_rules_cost())

    @classmethod
    def for_iot_device(cls) -> "OntologyValidator":
        """MEDI IoT Gateway — 기기·측정값 검증"""
        return cls(domain=OntologyDomain.IOT_DEVICE,
                   rules=cls._default_rules_iot_device())

    @classmethod
    def for_health_data(cls) -> "OntologyValidator":
        """통합 건강 데이터 시계열"""
        return cls(domain=OntologyDomain.HEALTH_DATA,
                   rules=cls._default_rules_health_data())

    @staticmethod
    def _load_default_rules(domain: OntologyDomain) -> dict:
        """도메인별 기본 규칙 반환"""
        if domain == OntologyDomain.IOT_DEVICE:
            return OntologyValidator._default_rules_iot_device()
        if domain == OntologyDomain.HEALTH_DATA:
            return OntologyValidator._default_rules_health_data()
        if domain == OntologyDomain.MEDICAL:
            return {
                "semantic": {
                    "icd10_fields": ["diagnosis_code", "secondary_diagnosis", "icd10_code"],
                    "terminology_fields": {
                        "eye_condition": [
                            "diabetic_retinopathy", "macular_degeneration",
                            "glaucoma", "cataract", "normal",
                            "retinal_detachment", "hypertensive_retinopathy",
                        ],
                        "laterality": ["OD", "OS", "OU"],
                        "severity": ["mild", "moderate", "severe", "critical"],
                    },
                    # MED-SEM-003 — 안과 진단인데 비안과 ICD-10 코드 사용 시 warning
                    "allowed_icd10_categories": [
                        "H35", "H36", "H40", "H53",      # 망막·녹내장·시각장애 등
                        "H25", "H26", "H27",              # 백내장 계열
                        "E11", "E10",                     # 당뇨 (망막증 연계)
                        "I10",                            # 본태성고혈압 (고혈압망막증)
                    ],
                    # MED-SEM-004 — confidence field name
                    "confidence_field": "confidence",
                    # MED-SEM-005 — severity → urgency 임상 매핑
                    "severity_urgency_map": {
                        "mild":     ["routine"],
                        "moderate": ["routine", "urgent"],
                        "severe":   ["urgent", "emergency"],
                        "critical": ["emergency"],
                    },
                    # MED-SEM-007 — 인증된 의료 모델 화이트리스트 (warning)
                    "model_whitelist": [
                        "google/gemma-4-26b-a4b",         # primary HEAVY
                        "google/gemma-4-e4b",             # LOCAL_FAST
                        "openai/gpt-oss-20b",             # consensus member
                        "qwen/qwen3-4b-2507",             # consensus member
                    ],
                    "glaucoma_model_whitelist": [
                        "efficientnet_b4_glaucoma",
                        "retinal_glaucoma",
                    ],
                    "glaucoma_semantic": {
                        "referral_risk_map": {
                            "HIGH": "immediate",
                            "MODERATE": "routine",
                            "LOW": "none",
                        },
                    },
                },
                "structural": {
                    "required_fields": [
                        "patient_id", "diagnosis_code",
                        "examination_date", "doctor_id",
                        "eye_condition", "confidence",
                        "severity", "model_used", "ontology_passed",
                    ],
                    "field_types": {
                        "patient_id":     "str",
                        "age":            "int",
                        "vision_od":      "number",
                        "vision_os":      "number",
                        "confidence":     "number",
                        "iop_od":         "number",
                        "iop_os":         "number",
                        "ontology_passed": "bool",
                    },
                    "field_formats": {
                        "patient_id":       r"^P\d{6}$",
                        "examination_date": r"^\d{4}-\d{2}-\d{2}$",
                        "birth_date":       r"^\d{4}-\d{2}-\d{2}$",
                        "doctor_id":        r"^D\d{4,8}$",
                    },
                    "forbidden_fields": [
                        "ssn", "social_security", "password",
                        "credit_card", "card_number", "rrn",
                    ],
                },
                "constraints": {
                    "ranges": {
                        "age":           {"min": 0,   "max": 150},
                        "vision_od":     {"min": 0.0, "max": 2.0},
                        "vision_os":     {"min": 0.0, "max": 2.0},
                        "iop_od":        {"min": 0,   "max": 80},
                        "iop_os":        {"min": 0,   "max": 80},
                        "confidence":    {"min": 0.0, "max": 1.0},
                        "hba1c":         {"min": 3.0, "max": 20.0},
                        "blood_glucose": {"min": 30,  "max": 600},
                        "bp_systolic":   {"min": 60,  "max": 260},
                        "bp_diastolic":  {"min": 30,  "max": 160},
                    },
                    "date_fields": ["examination_date", "birth_date", "surgery_date"],
                    "enums": {
                        "laterality":  ["OD", "OS", "OU"],
                        "urgency":     ["routine", "urgent", "emergency"],
                        "severity":    ["mild", "moderate", "severe", "critical"],
                        "report_status": ["pending", "draft", "approved", "rejected"],
                    },
                },
                "dependencies": {
                    "requires": [
                        {
                            "if_field":     "diagnosis_code",
                            "then_require": "examination_date",
                            "message":      "진단 코드가 있으면 검사 날짜가 필수입니다.",
                        },
                        {
                            "if_field":     "surgery_date",
                            "then_require": "anesthesia_type",
                            "message":      "수술 날짜가 있으면 마취 유형이 필수입니다.",
                        },
                        {
                            "if_field":     "eye_condition",
                            "has_value":    "diabetic_retinopathy",
                            "then_require": "blood_glucose",
                            "message":      "당뇨망막병증 진단 시 혈당 수치가 필수입니다.",
                        },
                        {
                            "if_field":     "eye_condition",
                            "has_value":    "glaucoma",
                            "then_require": "iop_od",
                            "message":      "녹내장 진단 시 안압 측정 (최소 OD) 이 필수입니다.",
                        },
                        {
                            "if_field":     "eye_condition",
                            "has_value":    "macular_degeneration",
                            "then_require": "vision_od",
                            "message":      "황반변성 진단 시 시력 (OD) 기록이 필수입니다.",
                        },
                        {
                            "if_field":     "urgency",
                            "has_value":    "emergency",
                            "then_require": "severity",
                            "message":      "emergency 케이스는 severity 가 명시되어야 합니다.",
                        },
                        {
                            "if_field":     "ontology_passed",
                            "has_value":    True,
                            "then_require": "model_used",
                            "message":      "ontology_passed=True 는 model_used 가 기록되어야 합니다.",
                        },
                    ],
                    "excludes": [],
                },
            }

        elif domain == OntologyDomain.SOFTWARE:
            return {
                "semantic": {
                    "naming_fields": ["function_name", "variable_name", "class_name"],
                    "forbidden_names": [
                        "x", "y", "z", "a", "b", "c",
                        "tmp", "temp", "foo", "bar", "baz", "data2",
                    ],
                },
                "structural": {
                    "required_fields": [
                        "function_name", "parameters", "return_type",
                    ],
                    "field_types": {
                        "function_name": "str",
                        "parameters":    "list",
                        "line_count":    "int",
                        "complexity":    "int",
                    },
                    "forbidden_fields": [
                        "hardcoded_password", "api_key_inline",
                    ],
                },
                "constraints": {
                    "ranges": {
                        "line_count":       {"min": 1,  "max": 50},   # 함수 최대 50줄
                        "complexity":       {"min": 1,  "max": 10},   # 순환 복잡도
                        "parameter_count":  {"min": 0,  "max": 5},    # 파라미터 최대 5개
                        "nesting_depth":    {"min": 0,  "max": 4},    # 중첩 깊이
                    },
                    "lengths": {
                        "function_name": {"min": 3, "max": 50},
                    },
                    "enums": {
                        "language": ["python", "javascript", "typescript", "go"],
                    },
                },
                "dependencies": {
                    "requires": [
                        {
                            "if_field":    "is_async",
                            "has_value":   True,
                            "then_require": "has_await",
                            "message":     "async 함수는 await를 사용해야 합니다.",
                        },
                        {
                            "if_field":    "has_return_value",
                            "has_value":   True,
                            "then_require": "return_type",
                            "message":     "반환값이 있으면 return_type 명시가 필요합니다.",
                        },
                    ],
                    "excludes": [],
                },
            }

        elif domain == OntologyDomain.BUSINESS:
            return {
                "semantic": {
                    "status_fields": {
                        "contract_status": [
                            "draft", "review", "approved", "rejected",
                            "executed", "terminated",
                        ],
                        "approval_status": [
                            "pending", "approved", "rejected", "escalated",
                        ],
                    },
                },
                "structural": {
                    "required_fields": [
                        "contract_id", "requester_id",
                        "request_date", "description",
                    ],
                    "field_types": {
                        "contract_id":  "str",
                        "amount":       "float",
                        "requester_id": "str",
                        "risk_level":   "str",
                        "approver_id":  "str",
                    },
                    "field_formats": {
                        "contract_id":   r"^CON-\d{8}$",   # CON-20260508
                        "request_date":  r"^\d{4}-\d{2}-\d{2}$",
                    },
                    "forbidden_fields": [],
                },
                "constraints": {
                    "ranges": {
                        "amount":         {"min": 0,    "max": 1_000_000_000},
                        "approval_level": {"min": 1,    "max": 5},
                    },
                    "date_fields": ["request_date", "approval_date", "expiry_date"],
                    "enums": {
                        "currency":  ["KRW", "USD", "EUR", "JPY"],
                        "priority":  ["low", "normal", "high", "critical"],
                        "risk_level": [
                            "low", "medium", "high", "critical",
                        ],
                    },
                },
                "dependencies": {
                    "requires": [
                        {
                            "if_field":    "approval_status",
                            "has_value":   "approved",
                            "then_require": "approver_id",
                            "message":     "승인 상태이면 승인자 ID가 필수입니다.",
                        },
                        {
                            "if_field":    "amount",
                            "then_require": "currency",
                            "message":     "금액이 있으면 통화 단위가 필수입니다.",
                        },
                        {
                            "if_field":    "refund_request",
                            "then_require": "original_contract_id",
                            "message":     "환불 요청 시 원계약 ID가 필수입니다.",
                        },
                        {
                            "if_field":    "risk_level",
                            "has_value":   "high",
                            "then_require": "approver_id",
                            "message":     "위험도 high이면 승인자 ID가 필수입니다.",
                        },
                        {
                            "if_field":    "risk_level",
                            "has_value":   "critical",
                            "then_require": "approver_id",
                            "message":     "위험도 critical이면 승인자 ID가 필수입니다.",
                        },
                    ],
                    "excludes": [],
                },
            }

        elif domain == OntologyDomain.SVG:
            return OntologyValidator._default_rules_svg()

        elif domain == OntologyDomain.POLYGLOT:
            return OntologyValidator._default_rules_polyglot("python")

        elif domain == OntologyDomain.KNOWLEDGE:
            return OntologyValidator._default_rules_knowledge()

        elif domain == OntologyDomain.COST:
            return OntologyValidator._default_rules_cost()

        # GENERAL — 최소 규칙
        return {
            "semantic":     {},
            "structural":   {"required_fields": []},
            "constraints":  {},
            "dependencies": {},
        }

    @staticmethod
    def _default_rules_svg() -> dict:
        return {
            "semantic": {
                "svg_content_field": "svg_content",
                "svg_max_elements": 1000,
            },
            "structural": {
                "required_fields": ["svg_content", "svg_type"],
                "field_types": {
                    "svg_content": "str",
                    "svg_type":    "str",
                    "no_pii":      "bool",
                },
            },
            "constraints": {
                "enums": {
                    "svg_type": [
                        "flowchart", "architecture", "sequence", "er_diagram",
                        "medical_report", "business_process",
                    ],
                },
                "byte_lengths": {
                    "svg_content": {"max": 512000},
                },
            },
            "dependencies": {
                "requires": [
                    {
                        "if_field": "svg_type",
                        "has_value": "medical_report",
                        "then_require": "no_pii",
                        "then_require_truthy": True,
                        "message": "svg_type=medical_report일 때 no_pii=true가 필수입니다.",
                    },
                ],
            },
        }

    @staticmethod
    def _default_rules_polyglot(language: str) -> dict:
        return {
            "semantic": {
                "polyglot_language": language,
                "code_field":        "code",
            },
            "structural": {
                "required_fields": ["code"],
                "field_types": {
                    "code":          "str",
                    "function_name": "str",
                },
            },
            "constraints": {
                "lengths": {
                    "code": {"max": 200_000},
                },
            },
            "dependencies": {},
        }

    @staticmethod
    def _default_rules_knowledge() -> dict:
        return {
            "semantic": {},
            "structural": {
                "required_fields": ["task", "language", "result", "success"],
                "field_types": {
                    "task":     "str",
                    "language": "str",
                    "result":   "str",
                    "success":  "bool",
                },
            },
            "constraints": {
                "lengths": {
                    "task":   {"min": 10, "max": 500},
                    "result": {"max": 10_000},
                },
                "enums": {
                    "language": ["python", "typescript", "rust"],
                },
                "list_lengths": {
                    "embedding": {"len": 768},
                },
            },
            "dependencies": {
                "requires": [
                    {
                        "if_field": "success",
                        "has_value": False,
                        "then_require": "error_message",
                        "message": "success=false일 때 error_message가 필수입니다.",
                    },
                ],
            },
        }

    @staticmethod
    def _default_rules_iot_device() -> dict:
        device_types = [
            "tonometer", "oct", "perimeter", "wearable", "cgm", "bp_monitor",
        ]
        return {
            "semantic": {"device_type_vocab": device_types},
            "structural": {
                "required_fields": [
                    "patient_id", "device_id", "device_type", "recorded_at",
                ],
                "field_types": {
                    "patient_id": "str",
                    "device_id": "str",
                    "device_type": "str",
                    "iop_mmhg": "number",
                    "blood_glucose_mg_dl": "number",
                    "bp_systolic": "number",
                    "bp_diastolic": "number",
                },
            },
            "constraints": {
                "enums": {"device_type": device_types},
                "ranges": {
                    "iop_mmhg": {"min": 0, "max": 80},
                    "blood_glucose_mg_dl": {"min": 30, "max": 600},
                    "bp_systolic": {"min": 60, "max": 260},
                    "bp_diastolic": {"min": 30, "max": 160},
                },
            },
            "dependencies": {
                "clinical_alerts": {
                    "iop_high_threshold": 21,
                    "hyperglycemia_threshold": 180,
                },
            },
        }

    @staticmethod
    def _default_rules_health_data() -> dict:
        return {
            "semantic": {},
            "structural": {
                "required_fields": ["patient_id", "metric", "value", "recorded_at"],
                "field_types": {
                    "patient_id": "str",
                    "metric": "str",
                    "value": "number",
                },
            },
            "constraints": {
                "enums": {
                    "metric": ["iop", "glucose", "bp_sys", "bp_dia", "heart_rate"],
                },
            },
            "dependencies": {},
        }

    @staticmethod
    def _default_rules_cost() -> dict:
        return {
            "semantic": {},
            "structural": {
                "required_fields": [
                    "task", "complexity", "selected_model", "estimated_tokens",
                ],
                "field_types": {
                    "task":              "str",
                    "complexity":        "str",
                    "selected_model":    "str",
                    "estimated_tokens":  "int",
                },
            },
            "constraints": {
                "ranges": {
                    "estimated_tokens": {"min": 1, "max": 100_000_000},
                    "budget_usd":       {"min": 0, "max": 1e12},
                    "actual_cost_usd": {"min": 0, "max": 1e12},
                },
                "enums": {
                    "complexity": ["simple", "medium", "complex", "critical"],
                },
            },
            "dependencies": {
                "requires": [],
                "cost_policy": {
                    "enabled": True,
                    "heavy_markers": ("HEAVY", "CONSENSUS"),
                    "micro_budget_ceiling": 0.01,
                    "local_hints": (
                        "local",
                        "lm-studio",
                        "gemma-4-e4b",
                        "LOCAL_FAST",
                    ),
                },
            },
        }
