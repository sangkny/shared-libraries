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

        return result

    async def _validate_medical_semantic(self, data: dict, rules: dict,
                                          result: ValidationResult):
        """의료 데이터 의미 검증"""
        # ICD-10 코드 형식 검증 (예: H35.0, E11.9)
        icd10_fields = rules.get("icd10_fields", [])
        for field in icd10_fields:
            val = data.get(field, "")
            if val and not self._is_valid_icd10(val):
                result.add(self._error(
                    "MED-SEM-001",
                    f"ICD-10 코드 형식이 올바르지 않습니다: '{val}'",
                    field=field, value=val,
                    suggestion="올바른 형식 예시: H35.0 (황반변성), E11.9 (2형 당뇨)"
                ))

        # 허용된 의료 용어 검증
        terminology_fields = rules.get("terminology_fields", {})
        for field, allowed_terms in terminology_fields.items():
            val = data.get(field, "")
            if val and val not in allowed_terms:
                result.add(self._warning(
                    "MED-SEM-002",
                    f"표준 의료 용어가 아닙니다: '{val}'",
                    field=field, value=val,
                    suggestion=f"허용 용어: {', '.join(allowed_terms[:5])}"
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
        return {"medical": "MED", "software": "SW",
                "business": "BIZ", "general": "GEN"}.get(self.domain.value, "GEN")

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
                if required_val is None or required_val == "":
                    result.add(self._error(
                        "DEP-001",
                        message or (
                            f"'{trigger_field}'이 설정되면 "
                            f"'{required_field}'도 필수입니다."
                        ),
                        field=required_field,
                        suggestion=f"'{required_field}' 필드를 입력하세요."
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

        return result

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

    # ── 기본 도메인 규칙 ──────────────────────────────────

    @staticmethod
    def _load_default_rules(domain: OntologyDomain) -> dict:
        """도메인별 기본 규칙 반환"""
        if domain == OntologyDomain.MEDICAL:
            return {
                "semantic": {
                    "icd10_fields": ["diagnosis_code", "secondary_diagnosis"],
                    "terminology_fields": {
                        "eye_condition": [
                            "diabetic_retinopathy", "macular_degeneration",
                            "glaucoma", "cataract", "normal",
                        ],
                        "laterality": ["OD", "OS", "OU"],  # 우안/좌안/양안
                    },
                },
                "structural": {
                    "required_fields": [
                        "patient_id", "diagnosis_code",
                        "examination_date", "doctor_id",
                    ],
                    "field_types": {
                        "patient_id": "str",
                        "age":        "int",
                        "vision_od":  "float",
                        "vision_os":  "float",
                    },
                    "field_formats": {
                        "patient_id":       r"^P\d{6}$",   # P123456
                        "examination_date": r"^\d{4}-\d{2}-\d{2}$",
                    },
                    "forbidden_fields": [
                        "ssn", "social_security", "password",
                    ],
                },
                "constraints": {
                    "ranges": {
                        "age":        {"min": 0,   "max": 150},
                        "vision_od":  {"min": 0.0, "max": 2.0},
                        "vision_os":  {"min": 0.0, "max": 2.0},
                        "iop_od":     {"min": 0,   "max": 80},   # 안압 (mmHg)
                        "iop_os":     {"min": 0,   "max": 80},
                    },
                    "date_fields": ["examination_date", "birth_date"],
                    "enums": {
                        "laterality": ["OD", "OS", "OU"],
                        "urgency":    ["routine", "urgent", "emergency"],
                    },
                },
                "dependencies": {
                    "requires": [
                        {
                            "if_field":    "diagnosis_code",
                            "then_require": "examination_date",
                            "message":     "진단 코드가 있으면 검사 날짜가 필수입니다.",
                        },
                        {
                            "if_field":    "surgery_date",
                            "then_require": "anesthesia_type",
                            "message":     "수술 날짜가 있으면 마취 유형이 필수입니다.",
                        },
                        {
                            "if_field":    "eye_condition",
                            "has_value":   "diabetic_retinopathy",
                            "then_require": "blood_glucose",
                            "message":     "당뇨망막병증 진단 시 혈당 수치가 필수입니다.",
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
                    ],
                    "excludes": [],
                },
            }

        # GENERAL — 최소 규칙
        return {
            "semantic":     {},
            "structural":   {"required_fields": []},
            "constraints":  {},
            "dependencies": {},
        }
