# shared-libraries/tests/test_integration.py
"""
통합 테스트 — Docker 컨테이너 내부에서 실행
LLM(LM Studio) + Ontology + Agent 전체 흐름 검증

실행:
    docker compose -f docker-compose.dev.yml run --rm shared-libs \
        pytest tests/test_integration.py -v
"""
import os
import pytest
import asyncio
import httpx

# ── 환경 확인 ─────────────────────────────────────────────
LLM_BASE_URL = os.getenv("LOCAL_BASE_URL", "http://host.docker.internal:8000/v1")
LLM_MODEL    = os.getenv("LOCAL_FAST_MODEL", "google/gemma-4-e4b")


# ════════════════════════════════════════════════════════════
# LEVEL 1: 환경 연결 테스트
# ════════════════════════════════════════════════════════════
class TestEnvironment:
    """Docker 컨테이너 기본 환경 확인"""

    def test_python_version(self):
        import sys
        assert sys.version_info >= (3, 11), "Python 3.11+ 필요"
        print(f"\n  Python: {sys.version}")

    def test_required_packages(self):
        """필수 패키지 import 확인"""
        import openai
        import httpx
        print(f"\n  openai: {openai.__version__}")
        print(f"  httpx:  {httpx.__version__}")

    def test_env_variables(self):
        """환경변수 확인"""
        url = os.getenv("LOCAL_BASE_URL")
        assert url is not None, "LOCAL_BASE_URL 환경변수 없음"
        assert "8000" in url, f"포트 8000 확인 필요: {url}"
        print(f"\n  LOCAL_BASE_URL: {url}")
        print(f"  LLM_PROVIDER:   {os.getenv('LLM_PROVIDER', 'local')}")

    @pytest.mark.asyncio
    async def test_lm_studio_connection(self):
        """LM Studio API 연결 확인 (host.docker.internal:8000)"""
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                r = await client.get(f"{LLM_BASE_URL}/models")
                assert r.status_code == 200, f"연결 실패: {r.status_code}"
                models = [m["id"] for m in r.json().get("data", [])]
                assert len(models) > 0, "모델이 없음"
                print(f"\n  ✅ LM Studio 연결 성공")
                print(f"  모델 목록: {models}")
            except httpx.ConnectError:
                pytest.fail(
                    "LM Studio에 연결할 수 없습니다.\n"
                    "확인사항:\n"
                    "  1. LM Studio가 실행 중인지 확인\n"
                    "  2. Server가 0.0.0.0:8000 으로 열려있는지 확인\n"
                    f"  3. URL: {LLM_BASE_URL}"
                )

    @pytest.mark.asyncio
    async def test_target_model_available(self):
        """gemma-4-e4b 모델 로드 확인"""
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{LLM_BASE_URL}/models")
            models = [m["id"] for m in r.json().get("data", [])]
            assert LLM_MODEL in models, (
                f"모델 '{LLM_MODEL}'이 LM Studio에 로드되지 않음\n"
                f"현재 모델: {models}"
            )
            print(f"\n  ✅ {LLM_MODEL} 모델 확인")


# ════════════════════════════════════════════════════════════
# LEVEL 2: Ontology 단독 테스트
# ════════════════════════════════════════════════════════════
class TestOntologyIntegration:
    """OntologyValidator 실제 동작 확인"""

    @pytest.mark.asyncio
    async def test_medical_valid_data(self):
        """정상 의료 데이터 → PASS"""
        from ontology.validator import OntologyValidator

        v = OntologyValidator.for_medical()
        result = await v.validate({
            "patient_id":       "P123456",
            "diagnosis_code":   "H35.0",
            "examination_date": "2026-05-08",
            "doctor_id":        "DR001",
            "eye_condition":    "diabetic_retinopathy",
            "blood_glucose":    180.5,
            "vision_od":        0.5,
            "vision_os":        0.6,
        })
        print(f"\n  {result.summary}")
        assert result.passed is True
        assert result.error_count == 0

    @pytest.mark.asyncio
    async def test_medical_invalid_data(self):
        """오류 의료 데이터 → FAIL + 정확한 오류 코드"""
        from ontology.validator import OntologyValidator

        v = OntologyValidator.for_medical()
        result = await v.validate({
            "patient_id":     "WRONG_FORMAT",  # P123456 형식 아님
            "diagnosis_code": "INVALID_CODE",  # ICD-10 형식 아님
            "ssn":            "123456-1234567", # 금지 필드
            # examination_date 없음 (필수)
            # doctor_id 없음 (필수)
        })
        print(f"\n  {result.summary}")
        for e in result.errors:
            print(f"    ❌ {e}")
        assert result.passed is False
        assert result.error_count >= 3

    @pytest.mark.asyncio
    async def test_software_valid_data(self):
        """정상 소프트웨어 데이터 → PASS"""
        from ontology.validator import OntologyValidator

        v = OntologyValidator.for_software()
        result = await v.validate({
            "function_name":   "calculate_bmi",
            "parameters":      ["weight", "height"],
            "return_type":     "float",
            "line_count":      15,
            "complexity":      3,
            "parameter_count": 2,
            "language":        "python",
        })
        print(f"\n  {result.summary}")
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_dependency_validation(self):
        """당뇨망막병증 → 혈당 수치 필수 (Dependency 검증)"""
        from ontology.validator import OntologyValidator

        v = OntologyValidator.for_medical()
        result = await v.validate({
            "patient_id":       "P123456",
            "diagnosis_code":   "E11.9",
            "examination_date": "2026-05-08",
            "doctor_id":        "DR001",
            "eye_condition":    "diabetic_retinopathy",
            # blood_glucose 없음! → DEP-001 오류
        })
        print(f"\n  {result.summary}")
        dep_errors = [e for e in result.errors if e.code == "DEP-001"]
        assert len(dep_errors) > 0, "Dependency 오류가 감지되지 않음"
        print(f"    ✅ DEP-001 감지: {dep_errors[0].message}")


# ════════════════════════════════════════════════════════════
# LEVEL 3: LLM 연동 테스트 (실제 API 호출)
# ════════════════════════════════════════════════════════════
class TestLLMIntegration:
    """LLMClient → LM Studio 실제 호출"""

    @pytest.mark.asyncio
    async def test_basic_chat(self):
        """LLMClient.chat() 실제 호출"""
        from llm.client import LLMClient
        from llm.base import ModelRole

        client = LLMClient()
        res = await client.chat(
            "안녕하세요. 한 문장으로 대답해주세요.",
            role=ModelRole.FAST,
            max_tokens=50,
        )
        print(f"\n  모델: {res.model_used}")
        print(f"  응답: {res.content[:100]}")
        print(f"  지연: {res.latency_ms:.0f}ms")
        assert res.content != ""
        assert res.model_used == LLM_MODEL
        assert res.latency_ms > 0

    @pytest.mark.asyncio
    async def test_embed(self):
        """임베딩 실제 호출"""
        from llm.client import LLMClient

        client = LLMClient()
        res = await client.embed("당뇨망막병증 진단 기준")
        print(f"\n  임베딩 차원: {res.dimensions}")
        print(f"  모델: {res.model_used}")
        assert res.dimensions > 0
        assert len(res.embedding) == res.dimensions

    @pytest.mark.asyncio
    async def test_health_check(self):
        """Provider 상태 확인"""
        from llm.client import LLMClient

        client = LLMClient()
        status = client.health_check_all()
        print(f"\n  상태: {status}")
        assert status["main"]["status"] == "ok"


# ════════════════════════════════════════════════════════════
# LEVEL 4: Agent + LLM 통합 테스트
# ════════════════════════════════════════════════════════════
class TestAgentLLMIntegration:
    """Agent → LLM(LM Studio) 실제 호출"""

    @pytest.mark.asyncio
    async def test_planner_with_real_llm(self):
        """PlannerAgent 실제 LLM 호출"""
        from agents.planner import PlannerAgent
        from ontology.base import OntologyDomain

        planner = PlannerAgent(domain=OntologyDomain.SOFTWARE)
        result  = await planner.run(
            "두 수를 더하는 add 함수를 Python으로 구현하세요."
        )
        print(f"\n  상태: {result.status.value}")
        print(f"  모델: {result.model_used}")
        if result.success:
            plan = result.output
            print(f"  단계 수: {len(plan.steps)}")
            for i, step in enumerate(plan.steps, 1):
                print(f"    {i}. {step}")
        assert result.success, f"PlannerAgent 실패: {result.error}"

    @pytest.mark.asyncio
    async def test_pipeline_software(self):
        """
        Orchestrator PIPELINE 전략 실제 실행
        Planner → Generator → Reviewer → (Fixer)
        """
        from agents.orchestrator import Orchestrator, OrchestraStrategy
        from ontology.base import OntologyDomain

        orch = Orchestrator(
            domain=OntologyDomain.SOFTWARE,
            strategy=OrchestraStrategy.PIPELINE,
            max_iterations=2,
        )
        result = await orch.execute(
            "두 정수를 입력받아 합을 반환하는 add(a, b) 함수를 "
            "타입 힌트와 docstring 포함하여 Python으로 구현하세요."
        )
        print(f"\n  {result.summary}")
        print(f"  Lore 항목: {len(result.lore)}개")
        if result.output:
            print(f"  결과물 일부:\n    {str(result.output)[:200]}")

        # 결과물이 있어야 함 (passed 여부와 무관)
        assert result.output is not None, "결과물이 없음"
        assert len(result.lore) >= 2, "Lore가 기록되지 않음"

    @pytest.mark.asyncio
    async def test_ontology_reviewer_integration(self):
        """
        ReviewerAgent + OntologyValidator 통합
        잘못된 의료 데이터 → Ontology 검증 실패 → 자동 감지
        """
        from agents.reviewer import ReviewerAgent
        from ontology.base import OntologyDomain

        reviewer = ReviewerAgent(domain=OntologyDomain.MEDICAL)

        # SSN이 포함된 잘못된 데이터
        bad_medical_data = {
            "patient_id":       "P123456",
            "diagnosis_code":   "H35.0",
            "examination_date": "2026-05-08",
            "doctor_id":        "DR001",
            "ssn":              "123456-1234567",  # 금지 필드!
        }
        result = await reviewer.run(
            "환자 데이터 검증",
            context={"generated": bad_medical_data}
        )
        review = result.output
        print(f"\n  검토 결과: {'PASS' if review.passed else 'FAIL'}")
        print(f"  피드백: {review.feedback[:100]}")
        if review.ontology_result:
            print(f"  Ontology: {review.ontology_result.summary}")

        # SSN 때문에 반드시 FAIL
        assert review.passed is False
        assert review.ontology_result is not None
