# shared-libraries/ontology/tests/test_validator.py
"""
OntologyValidator 테스트 — 3개 도메인 × 4개 Validator
실행: pytest ontology/tests/test_validator.py -v
"""
import pytest
from ..base import OntologyDomain, ValidatorType, Severity
from ..validator import OntologyValidator


# ════════════════════════════════════════════════════════════
# MEDICAL 도메인 테스트
# ════════════════════════════════════════════════════════════
class TestMedicalValidator:

    @pytest.fixture
    def validator(self):
        return OntologyValidator.for_medical()

    @pytest.fixture
    def valid_patient(self):
        return {
            "patient_id":       "P123456",
            "age":              65,
            "diagnosis_code":   "H36.0",
            "examination_date": "2026-05-08",
            "doctor_id":        "D00001",
            "eye_condition":    "diabetic_retinopathy",
            "blood_glucose":    180.5,
            "laterality":       "OU",
            "vision_od":        0.5,
            "vision_os":        0.6,
            "iop_od":           18,
            "iop_os":           17,
            # D R2 Day 2 — 신규 필수 임상 메타데이터
            "severity":         "moderate",
            "confidence":       0.82,
            "model_used":       "google/gemma-4-26b-a4b",
            "ontology_passed":  True,
            "urgency":          "urgent",
        }

    @pytest.mark.asyncio
    async def test_valid_patient_passes(self, validator, valid_patient):
        result = await validator.validate(valid_patient)
        assert result.passed is True
        assert result.error_count == 0
        print(f"\n{result.summary}")

    @pytest.mark.asyncio
    async def test_missing_required_field(self, validator):
        data = {
            "patient_id":     "P123456",
            "diagnosis_code": "H35.0",
            # examination_date 없음!
            "doctor_id":      "DR001",
        }
        result = await validator.validate(data)
        assert result.passed is False
        codes = [e.code for e in result.errors]
        assert any("STR" in c for c in codes)

    @pytest.mark.asyncio
    async def test_invalid_icd10_code(self, validator, valid_patient):
        valid_patient["diagnosis_code"] = "INVALID"
        result = await validator.validate(valid_patient)
        assert result.passed is False
        assert any(e.code == "MED-SEM-001" for e in result.errors)

    @pytest.mark.asyncio
    async def test_valid_icd10_codes(self, validator, valid_patient):
        """다양한 유효한 ICD-10 코드 테스트"""
        for code in ["H35.0", "E11.9", "H40.1", "H26.9"]:
            valid_patient["diagnosis_code"] = code
            result = await validator.validate(valid_patient)
            sem_errors = [e for e in result.errors if e.code == "MED-SEM-001"]
            assert len(sem_errors) == 0, f"유효한 ICD-10 코드 {code}가 거부됨"

    @pytest.mark.asyncio
    async def test_age_out_of_range(self, validator, valid_patient):
        valid_patient["age"] = 200  # 최대 150 초과
        result = await validator.validate(valid_patient)
        assert result.passed is False
        assert any(e.code == "CON-002" and "age" in e.field for e in result.errors)

    @pytest.mark.asyncio
    async def test_invalid_patient_id_format(self, validator, valid_patient):
        valid_patient["patient_id"] = "12345"  # P123456 형식 아님
        result = await validator.validate(valid_patient)
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_ssn_forbidden_field(self, validator, valid_patient):
        valid_patient["ssn"] = "123456-1234567"  # 금지 필드
        result = await validator.validate(valid_patient)
        assert result.passed is False
        assert any("ssn" in e.field for e in result.errors)

    @pytest.mark.asyncio
    async def test_diabetic_retinopathy_requires_blood_glucose(self, validator):
        """당뇨망막병증 진단 시 혈당 수치 필수 — dependency 검증"""
        data = {
            "patient_id":       "P123456",
            "diagnosis_code":   "E11.9",
            "examination_date": "2026-05-08",
            "doctor_id":        "DR001",
            "eye_condition":    "diabetic_retinopathy",
            # blood_glucose 없음!
        }
        result = await validator.validate(data)
        assert result.passed is False
        assert any("당뇨망막병증" in e.message for e in result.errors)

    @pytest.mark.asyncio
    async def test_invalid_vision_range(self, validator, valid_patient):
        valid_patient["vision_od"] = 3.0  # 최대 2.0 초과
        result = await validator.validate(valid_patient)
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_invalid_date_format(self, validator, valid_patient):
        valid_patient["examination_date"] = "2026/05/08"  # 잘못된 형식
        result = await validator.validate(valid_patient)
        assert result.passed is False

    # ── D R2 Day 2 — 신규 룰 검증 ────────────────────────

    @pytest.mark.asyncio
    async def test_non_ophthalmic_icd10_emits_med_sem_003(self, validator, valid_patient):
        """안과 진단인데 비안과 ICD-10 (예: A00) → MED-SEM-003 warning."""
        valid_patient["diagnosis_code"] = "A00.0"
        result = await validator.validate(valid_patient)
        warns = [w for w in result.warnings if w.code == "MED-SEM-003"]
        assert warns, f"MED-SEM-003 경고 미발생, warnings={[w.code for w in result.warnings]}"

    @pytest.mark.asyncio
    async def test_confidence_out_of_range_blocks(self, validator, valid_patient):
        """confidence > 1.0 → MED-SEM-004 error."""
        valid_patient["confidence"] = 1.5
        result = await validator.validate(valid_patient)
        assert result.passed is False
        assert any(e.code == "MED-SEM-004" for e in result.errors)

    @pytest.mark.asyncio
    async def test_low_confidence_warns_for_review(self, validator, valid_patient):
        """confidence < 0.5 → MED-SEM-004 warning (의사 검토 자동 승격 권장)."""
        valid_patient["confidence"] = 0.31
        result = await validator.validate(valid_patient)
        warn = [w for w in result.warnings if w.code == "MED-SEM-004"]
        assert warn

    @pytest.mark.asyncio
    async def test_severity_urgency_mismatch_critical_routine(self, validator, valid_patient):
        """severity=critical 인데 urgency=routine → MED-SEM-005 error."""
        valid_patient["severity"] = "critical"
        valid_patient["urgency"] = "routine"
        result = await validator.validate(valid_patient)
        assert any(e.code == "MED-SEM-005" for e in result.errors)

    @pytest.mark.asyncio
    async def test_laterality_finding_side_mismatch(self, validator, valid_patient):
        """laterality=OD 인데 finding_side=LEFT → MED-SEM-006 error."""
        valid_patient["laterality"] = "OD"
        valid_patient["finding_side"] = "LEFT"
        result = await validator.validate(valid_patient)
        assert any(e.code == "MED-SEM-006" for e in result.errors)

    @pytest.mark.asyncio
    async def test_unknown_model_used_warns(self, validator, valid_patient):
        """비인증 모델 → MED-SEM-007 warning."""
        valid_patient["model_used"] = "rando/experimental-llm-v0"
        result = await validator.validate(valid_patient)
        warns = [w for w in result.warnings if w.code == "MED-SEM-007"]
        assert warns

    @pytest.mark.asyncio
    async def test_glaucoma_requires_iop(self, validator):
        """녹내장 진단 시 IOP 측정 (최소 OD) 필수 (D R2 신규 dependency)."""
        data = {
            "patient_id":       "P123456",
            "diagnosis_code":   "H40.1",
            "examination_date": "2026-05-12",
            "doctor_id":        "D00001",
            "eye_condition":    "glaucoma",
            "severity":         "moderate",
            "confidence":       0.7,
            "model_used":       "google/gemma-4-26b-a4b",
            "ontology_passed":  True,
        }
        result = await validator.validate(data)
        assert result.passed is False
        assert any("녹내장" in e.message for e in result.errors)

    @pytest.mark.asyncio
    async def test_macular_degeneration_requires_vision(self, validator):
        """황반변성 진단 시 시력(OD) 기록 필수."""
        data = {
            "patient_id":       "P123456",
            "diagnosis_code":   "H35.3",
            "examination_date": "2026-05-12",
            "doctor_id":        "D00001",
            "eye_condition":    "macular_degeneration",
            "severity":         "mild",
            "confidence":       0.66,
            "model_used":       "google/gemma-4-26b-a4b",
            "ontology_passed":  True,
        }
        result = await validator.validate(data)
        assert result.passed is False
        assert any("황반변성" in e.message for e in result.errors)

    @pytest.mark.asyncio
    async def test_emergency_requires_severity(self, validator):
        """urgency=emergency → severity 필수."""
        data = {
            "patient_id":       "P123456",
            "diagnosis_code":   "H36.0",
            "examination_date": "2026-05-12",
            "doctor_id":        "D00001",
            "eye_condition":    "diabetic_retinopathy",
            "blood_glucose":    220,
            "confidence":       0.9,
            "model_used":       "google/gemma-4-26b-a4b",
            "ontology_passed":  True,
            "urgency":          "emergency",
            # severity 없음
        }
        result = await validator.validate(data)
        assert result.passed is False
        assert any("emergency" in e.message.lower() or "severity" in str(e.field).lower() for e in result.errors)

    @pytest.mark.asyncio
    async def test_hba1c_range_validation(self, validator, valid_patient):
        """hba1c 30 → 범위(3~20) 초과 → CON-002."""
        valid_patient["hba1c"] = 30.0
        result = await validator.validate(valid_patient)
        assert result.passed is False
        assert any(e.code == "CON-002" and e.field == "hba1c" for e in result.errors)


# ════════════════════════════════════════════════════════════
# SOFTWARE 도메인 테스트
# ════════════════════════════════════════════════════════════
class TestSoftwareValidator:

    @pytest.fixture
    def validator(self):
        return OntologyValidator.for_software()

    @pytest.fixture
    def valid_function(self):
        return {
            "function_name":    "calculate_bmi",
            "parameters":       ["weight", "height"],
            "return_type":      "float",
            "line_count":       15,
            "complexity":       3,
            "parameter_count":  2,
            "nesting_depth":    2,
            "language":         "python",
            "is_async":         False,
            "has_return_value": True,
        }

    @pytest.mark.asyncio
    async def test_valid_function_passes(self, validator, valid_function):
        result = await validator.validate(valid_function)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_function_too_long(self, validator, valid_function):
        valid_function["line_count"] = 100  # 최대 50줄 초과
        result = await validator.validate(valid_function)
        assert result.passed is False
        assert any(e.code == "CON-002" and "line_count" in e.field
                   for e in result.errors)

    @pytest.mark.asyncio
    async def test_high_complexity(self, validator, valid_function):
        valid_function["complexity"] = 15  # 최대 10 초과
        result = await validator.validate(valid_function)
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_meaningless_variable_name(self, validator, valid_function):
        valid_function["function_name"] = "tmp"
        result = await validator.validate(valid_function)
        assert result.passed is False
        assert any(e.code == "SW-SEM-002" for e in result.errors)

    @pytest.mark.asyncio
    async def test_async_without_await(self, validator, valid_function):
        valid_function["is_async"]  = True
        valid_function["has_await"] = None  # await 없음!
        result = await validator.validate(valid_function)
        assert result.passed is False
        assert any("async" in e.message for e in result.errors)

    @pytest.mark.asyncio
    async def test_too_many_parameters(self, validator, valid_function):
        valid_function["parameter_count"] = 8  # 최대 5개 초과
        result = await validator.validate(valid_function)
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_return_type_required_when_has_return(self, validator, valid_function):
        valid_function["has_return_value"] = True
        del valid_function["return_type"]   # return_type 없음!
        result = await validator.validate(valid_function)
        assert result.passed is False


# ════════════════════════════════════════════════════════════
# BUSINESS 도메인 테스트
# ════════════════════════════════════════════════════════════
class TestBusinessValidator:

    @pytest.fixture
    def validator(self):
        return OntologyValidator.for_business()

    @pytest.fixture
    def valid_contract(self):
        return {
            "contract_id":     "CON-20260508",
            "requester_id":    "EMP001",
            "request_date":    "2026-05-08",
            "description":     "소프트웨어 개발 서비스 계약",
            "amount":          5000000.0,
            "currency":        "KRW",
            "contract_status": "draft",
            "priority":        "normal",
        }

    @pytest.mark.asyncio
    async def test_valid_contract_passes(self, validator, valid_contract):
        result = await validator.validate(valid_contract)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_invalid_contract_id_format(self, validator, valid_contract):
        valid_contract["contract_id"] = "CONTRACT-001"  # 형식 불일치
        result = await validator.validate(valid_contract)
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_approved_without_approver(self, validator, valid_contract):
        valid_contract["approval_status"] = "approved"
        # approver_id 없음!
        result = await validator.validate(valid_contract)
        assert result.passed is False
        assert any("승인자" in e.message for e in result.errors)

    @pytest.mark.asyncio
    async def test_amount_without_currency(self, validator, valid_contract):
        del valid_contract["currency"]  # 통화 없음!
        result = await validator.validate(valid_contract)
        assert result.passed is False
        assert any("통화" in e.message for e in result.errors)

    @pytest.mark.asyncio
    async def test_invalid_status(self, validator, valid_contract):
        valid_contract["contract_status"] = "unknown_status"
        result = await validator.validate(valid_contract)
        assert result.passed is False


# ════════════════════════════════════════════════════════════
# OntologyValidator 공통 기능 테스트
# ════════════════════════════════════════════════════════════
class TestOntologyValidatorCommon:

    @pytest.mark.asyncio
    async def test_factory_for_domain_string(self):
        v = OntologyValidator.for_domain("medical")
        assert v.domain == OntologyDomain.MEDICAL

    @pytest.mark.asyncio
    async def test_partial_validation(self):
        """특정 Validator만 선택 실행"""
        v = OntologyValidator.for_medical()
        data = {
            "patient_id": "WRONG_FORMAT",
            "diagnosis_code": "H35.0",
            "examination_date": "2026-05-08",
            "doctor_id": "DR001",
        }
        # StructuralValidator만 실행
        result = await v.validate_partial(data, [ValidatorType.STRUCTURAL])
        assert result.domain == OntologyDomain.MEDICAL

    @pytest.mark.asyncio
    async def test_validation_result_to_dict(self):
        """ValidationResult.to_dict() 직렬화"""
        v = OntologyValidator.for_medical()
        result = await v.validate({"patient_id": "WRONG"})
        d = result.to_dict()
        assert "passed" in d
        assert "errors" in d
        assert "domain" in d
        assert d["domain"] == "medical"

    @pytest.mark.asyncio
    async def test_update_rules_runtime(self):
        """런타임 규칙 업데이트"""
        v = OntologyValidator.for_medical()
        v.update_rules({
            "structural": {
                "required_fields": ["patient_id"],  # 간소화
            }
        })
        # 업데이트된 규칙으로 검증
        result = await v.validate({"patient_id": "P123456"})
        assert result is not None

    def test_validation_result_summary(self):
        from ..base import ValidationResult, OntologyDomain
        r = ValidationResult(passed=True, domain=OntologyDomain.MEDICAL)
        assert "PASS" in r.summary
        assert "medical" in r.summary
