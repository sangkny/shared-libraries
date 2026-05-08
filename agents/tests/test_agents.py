# shared-libraries/agents/tests/test_agents.py
"""
Agent Framework 테스트
실행: pytest agents/tests/test_agents.py -v
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from ..base import AgentType, AgentStatus, LoreEntry
from ..planner import PlannerAgent, ExecutionPlan
from ..generator import GeneratorAgent
from ..reviewer import ReviewerAgent, ReviewResult
from ..fixer import FixerAgent
from ..orchestrator import (
    Orchestrator, OrchestraStrategy, create_orchestrator
)
from ...llm.base import ModelRole, LLMResponse, LLMProvider
from ...ontology.base import OntologyDomain


# ── Mock LLM 응답 헬퍼 ────────────────────────────────────
def mock_llm_response(content: str, model: str = "gemma-4-e4b") -> LLMResponse:
    return LLMResponse(
        content=content,
        model_used=model,
        provider=LLMProvider.LOCAL,
        role=ModelRole.FAST,
        latency_ms=100.0,
    )

def make_mock_llm(responses: list[str]) -> MagicMock:
    """순서대로 응답하는 Mock LLM"""
    mock = MagicMock()
    mock.chat = AsyncMock(side_effect=[
        mock_llm_response(r) for r in responses
    ])
    mock.embed = AsyncMock()
    return mock


# ════════════════════════════════════════════════════════════
# PlannerAgent 테스트
# ════════════════════════════════════════════════════════════
class TestPlannerAgent:

    @pytest.mark.asyncio
    async def test_basic_plan(self):
        mock_llm = make_mock_llm([
            """STEPS:
1. 환자 데이터 수집
2. 이미지 전처리
3. AI 모델 분석
4. 보고서 생성

CONSTRAINTS:
- 개인정보 보호 준수
- ICD-10 표준 사용

ITERATIONS: 2"""
        ])
        planner = PlannerAgent(domain=OntologyDomain.MEDICAL, llm=mock_llm)
        result  = await planner.run("안저 이미지 분석")

        assert result.success
        assert isinstance(result.output, ExecutionPlan)
        plan = result.output
        assert len(plan.steps) == 4
        assert len(plan.constraints) == 2
        assert plan.estimated_iterations == 2

    @pytest.mark.asyncio
    async def test_plan_with_context(self):
        mock_llm = make_mock_llm(["STEPS:\n1. 분석\n\nCONSTRAINTS:\n- 없음\n\nITERATIONS: 1"])
        planner  = PlannerAgent(domain=OntologyDomain.SOFTWARE, llm=mock_llm)
        result   = await planner.run("함수 구현", context={"previous_result": "이전 결과"})
        assert result.success

    @pytest.mark.asyncio
    async def test_plan_lore_recorded(self):
        mock_llm = make_mock_llm(["STEPS:\n1. 테스트\n\nCONSTRAINTS:\n\nITERATIONS: 1"])
        planner  = PlannerAgent(domain=OntologyDomain.MEDICAL, llm=mock_llm)
        await planner.run("테스트 작업")
        assert len(planner.lore) == 1
        assert planner.lore[0].action == "plan"

    def test_parse_plan_fallback(self):
        """파싱 실패 시 전체 응답을 단일 단계로"""
        plan = PlannerAgent._parse_plan("task", "그냥 텍스트 응답")
        assert len(plan.steps) >= 1


# ════════════════════════════════════════════════════════════
# GeneratorAgent 테스트
# ════════════════════════════════════════════════════════════
class TestGeneratorAgent:

    @pytest.mark.asyncio
    async def test_basic_generate(self):
        mock_llm  = make_mock_llm(["def calculate_bmi(weight: float, height: float) -> float:\n    return weight / (height ** 2)"])
        generator = GeneratorAgent(domain=OntologyDomain.SOFTWARE, llm=mock_llm)
        result    = await generator.run("calculate_bmi 함수 구현")

        assert result.success
        assert "calculate_bmi" in result.output

    @pytest.mark.asyncio
    async def test_generate_with_feedback(self):
        """피드백 반영 재생성"""
        mock_llm  = make_mock_llm(["개선된 코드"])
        generator = GeneratorAgent(domain=OntologyDomain.SOFTWARE, llm=mock_llm)
        result    = await generator.run(
            "함수 구현",
            context={"feedback": "타입 힌트 추가 필요", "iteration": 1}
        )
        assert result.success
        assert result.iteration == 1

    @pytest.mark.asyncio
    async def test_generate_with_plan(self):
        plan = ExecutionPlan(
            goal="함수 구현",
            steps=["1단계", "2단계"],
            domain="software",
        )
        mock_llm  = make_mock_llm(["결과물"])
        generator = GeneratorAgent(domain=OntologyDomain.SOFTWARE, llm=mock_llm)
        result    = await generator.run("함수 구현", context={"plan": plan})
        assert result.success


# ════════════════════════════════════════════════════════════
# ReviewerAgent 테스트
# ════════════════════════════════════════════════════════════
class TestReviewerAgent:

    @pytest.mark.asyncio
    async def test_review_pass(self):
        mock_llm = make_mock_llm([
            "VERDICT: PASS\nFEEDBACK: 코드가 잘 작성되었습니다.\nIMPROVEMENTS:\n- 없음"
        ])
        reviewer = ReviewerAgent(domain=OntologyDomain.SOFTWARE, llm=mock_llm)
        result   = await reviewer.run(
            "함수 구현",
            context={"generated": "def add(a: int, b: int) -> int:\n    return a + b"}
        )
        assert result.success
        review = result.output
        assert isinstance(review, ReviewResult)
        assert review.passed is True

    @pytest.mark.asyncio
    async def test_review_fail(self):
        mock_llm = make_mock_llm([
            "VERDICT: FAIL\nFEEDBACK: 타입 힌트가 없습니다.\nIMPROVEMENTS:\n- 타입 힌트 추가"
        ])
        reviewer = ReviewerAgent(domain=OntologyDomain.SOFTWARE, llm=mock_llm)
        result   = await reviewer.run(
            "함수 구현",
            context={"generated": "def add(a, b):\n    return a + b"}
        )
        review = result.output
        assert review.passed is False
        assert "타입" in review.feedback

    @pytest.mark.asyncio
    async def test_review_with_ontology_validation(self):
        """dict 데이터 → Ontology 자동 검증"""
        mock_llm = make_mock_llm(["VERDICT: PASS\nFEEDBACK: 좋습니다."])
        reviewer = ReviewerAgent(domain=OntologyDomain.MEDICAL, llm=mock_llm)

        # SSN 포함 — Ontology에서 forbidden field로 감지해야 함
        bad_data = {
            "patient_id": "P123456",
            "diagnosis_code": "H35.0",
            "examination_date": "2026-05-08",
            "doctor_id": "DR001",
            "ssn": "123456-1234567",  # 금지 필드!
        }
        result = await reviewer.run("환자 데이터 검증", context={"generated": bad_data})
        review = result.output
        assert review.passed is False  # SSN 때문에 FAIL


# ════════════════════════════════════════════════════════════
# FixerAgent 테스트
# ════════════════════════════════════════════════════════════
class TestFixerAgent:

    @pytest.mark.asyncio
    async def test_basic_fix(self):
        mock_llm = make_mock_llm([
            "def add(a: int, b: int) -> int:\n    \"\"\"두 수의 합\"\"\"\n    return a + b"
        ])
        fixer  = FixerAgent(domain=OntologyDomain.SOFTWARE, llm=mock_llm)
        review = ReviewResult(
            passed=False,
            feedback="타입 힌트와 docstring 추가 필요",
            improvement_hints=["타입 힌트 추가", "docstring 추가"],
        )
        result = await fixer.run(
            "add 함수 구현",
            context={"generated": "def add(a, b):\n    return a + b", "review": review}
        )
        assert result.success
        assert "int" in result.output  # 타입 힌트 추가됨

    @pytest.mark.asyncio
    async def test_fix_without_generated_fails(self):
        mock_llm = make_mock_llm([])
        fixer    = FixerAgent(domain=OntologyDomain.SOFTWARE, llm=mock_llm)
        result   = await fixer.run("task", context={})
        assert result.status == AgentStatus.FAILED


# ════════════════════════════════════════════════════════════
# Orchestrator 테스트
# ════════════════════════════════════════════════════════════
class TestOrchestrator:

    def _make_pipeline_llm(self):
        """PIPELINE 전략 실행을 위한 Mock LLM (plan + generate + review)"""
        return make_mock_llm([
            # Planner
            "STEPS:\n1. 분석\n2. 구현\n\nCONSTRAINTS:\n- 없음\n\nITERATIONS: 1",
            # Generator
            "def add(a: int, b: int) -> int:\n    return a + b",
            # Reviewer
            "VERDICT: PASS\nFEEDBACK: 좋습니다.\nIMPROVEMENTS:",
        ])

    @pytest.mark.asyncio
    async def test_pipeline_pass(self):
        orch   = Orchestrator(
            domain=OntologyDomain.SOFTWARE,
            strategy=OrchestraStrategy.PIPELINE,
            llm=self._make_pipeline_llm(),
        )
        result = await orch.execute("add 함수 구현")
        assert result.strategy == OrchestraStrategy.PIPELINE
        assert result.passed is True
        assert result.iterations == 1

    @pytest.mark.asyncio
    async def test_pipeline_retry_on_fail(self):
        """첫 번째 검토 실패 → Fixer → 두 번째 통과"""
        mock_llm = make_mock_llm([
            # Planner
            "STEPS:\n1. 구현\n\nCONSTRAINTS:\n\nITERATIONS: 2",
            # Generator (1st)
            "def add(a, b): return a+b",
            # Reviewer (1st) — FAIL
            "VERDICT: FAIL\nFEEDBACK: 타입 힌트 없음\nIMPROVEMENTS:\n- 타입 힌트",
            # Fixer
            "def add(a: int, b: int) -> int: return a + b",
            # Generator (2nd) — 재사용
            "def add(a: int, b: int) -> int: return a + b",
            # Reviewer (2nd) — PASS
            "VERDICT: PASS\nFEEDBACK: 좋습니다.\nIMPROVEMENTS:",
        ])
        orch   = Orchestrator(
            domain=OntologyDomain.SOFTWARE,
            strategy=OrchestraStrategy.PIPELINE,
            llm=mock_llm, max_iterations=2,
        )
        result = await orch.execute("add 함수 구현")
        assert result.passed is True
        assert result.iterations == 2

    @pytest.mark.asyncio
    async def test_fastest_strategy(self):
        mock_llm = make_mock_llm(["빠른 응답"])
        orch     = Orchestrator(
            domain=OntologyDomain.GENERAL,
            strategy=OrchestraStrategy.FASTEST,
            llm=mock_llm,
        )
        result = await orch.execute("빠른 응답 필요")
        assert result.strategy == OrchestraStrategy.FASTEST

    @pytest.mark.asyncio
    async def test_lore_collected(self):
        orch   = Orchestrator(
            domain=OntologyDomain.SOFTWARE,
            strategy=OrchestraStrategy.PIPELINE,
            llm=self._make_pipeline_llm(),
        )
        result = await orch.execute("add 함수 구현")
        assert len(result.lore) >= 1

    def test_create_orchestrator_factory(self):
        orch = create_orchestrator("medical", "consensus")
        assert orch.domain    == OntologyDomain.MEDICAL
        assert orch.strategy  == OrchestraStrategy.CONSENSUS

    def test_orchestrator_result_summary(self):
        from ..orchestrator import OrchestratorResult
        r = OrchestratorResult(
            task_id="test01",
            strategy=OrchestraStrategy.PIPELINE,
            domain=OntologyDomain.MEDICAL,
            passed=True, output="결과",
            iterations=2,
        )
        assert "PASS" in r.summary
        assert "pipeline" in r.summary
        assert "medical" in r.summary
