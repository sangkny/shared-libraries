# shared-libraries/harness/scenarios.py
"""
Harness 시나리오 정의
도메인별 테스트 케이스 — 실제 Agent 실행 + Ontology 검증

시나리오 구조:
    HarnessScenario
    ├── name:        시나리오 이름
    ├── domain:      OntologyDomain
    ├── strategy:    OrchestraStrategy
    ├── task:        Agent에게 줄 작업
    ├── validators:  검증 함수 목록
    └── expect_pass: 통과 기대 여부
"""
from dataclasses import dataclass, field
from typing import Callable, Any
from ontology.base import OntologyDomain


@dataclass
class HarnessScenario:
    """단일 Harness 테스트 시나리오"""
    name:           str
    domain:         OntologyDomain
    task:           str
    validators:     list[Callable[[Any], tuple[bool, str]]] = field(default_factory=list)
    strategy:       str   = "pipeline"
    expect_pass:    bool  = True
    tags:           list[str] = field(default_factory=list)
    timeout_sec:    int   = 180
    max_iterations: int   = 1   # smoke=1, 일반=2 (HEAVY 모델 호출 횟수 제한)


# ════════════════════════════════════════════════════════════
# 검증 함수 라이브러리
# ════════════════════════════════════════════════════════════

def has_content(output: Any) -> tuple[bool, str]:
    """결과물이 비어있지 않은지 확인"""
    ok = output is not None and str(output).strip() != ""
    return ok, "결과물이 있음" if ok else "결과물이 비어있음"

def has_type_hints(output: Any) -> tuple[bool, str]:
    """Python 타입 힌트 포함 여부"""
    code = str(output)
    ok = "->" in code or ": int" in code or ": str" in code or \
         ": float" in code or ": bool" in code or ": list" in code
    return ok, "타입 힌트 있음" if ok else "타입 힌트 없음"

def has_docstring(output: Any) -> tuple[bool, str]:
    """docstring 포함 여부"""
    code = str(output)
    ok = '"""' in code or "'''" in code
    return ok, "docstring 있음" if ok else "docstring 없음"

def has_def_keyword(output: Any) -> tuple[bool, str]:
    """함수 정의 포함 여부"""
    ok = "def " in str(output)
    return ok, "함수 정의 있음" if ok else "함수 정의 없음"

def no_pii_data(output: Any) -> tuple[bool, str]:
    """개인식별정보 없는지 확인"""
    text = str(output).lower()
    pii_keywords = ["ssn", "주민번호", "social_security", "password", "passwd"]
    found = [k for k in pii_keywords if k in text]
    ok = len(found) == 0
    return ok, "PII 없음" if ok else f"PII 발견: {found}"

def has_business_content(output: Any) -> tuple[bool, str]:
    """비즈니스 문서 내용 포함 여부"""
    text = str(output)
    ok = len(text) > 100
    return ok, f"비즈니스 내용 있음 ({len(text)}자)" if ok else "내용 너무 짧음"


# ════════════════════════════════════════════════════════════
# 도메인별 시나리오 정의
# ════════════════════════════════════════════════════════════

# ── SOFTWARE 시나리오 ─────────────────────────────────────
SOFTWARE_SCENARIOS = [
    HarnessScenario(
        name="simple_add_function",
        domain=OntologyDomain.SOFTWARE,
        strategy="pipeline",
        task="두 정수를 더하는 add(a, b) 함수를 타입 힌트와 docstring 포함하여 구현하세요.",
        validators=[has_content, has_def_keyword, has_type_hints],
        expect_pass=True,
        tags=["basic", "function", "smoke"],
        timeout_sec=200,
        max_iterations=1,   # smoke: 빠른 검증, 1회 실행
    ),
    HarnessScenario(
        name="bmi_calculator",
        domain=OntologyDomain.SOFTWARE,
        strategy="pipeline",
        task="체중(kg)과 키(m)를 입력받아 BMI를 계산하는 calculate_bmi(weight, height) 함수를 "
             "타입 힌트, docstring, 에러 처리 포함하여 구현하세요.",
        validators=[has_content, has_def_keyword, has_type_hints, has_docstring],
        expect_pass=True,
        tags=["intermediate", "function"],
        timeout_sec=300,
        max_iterations=2,
    ),
    HarnessScenario(
        name="debate_strategy",
        domain=OntologyDomain.SOFTWARE,
        strategy="debate",
        task="리스트에서 중복을 제거하는 remove_duplicates(items) 함수를 구현하세요.",
        validators=[has_content, has_def_keyword, has_type_hints],
        expect_pass=True,
        tags=["debate", "function"],
        timeout_sec=360,
        max_iterations=2,
    ),
]

# ── MEDICAL 시나리오 ──────────────────────────────────────
MEDICAL_SCENARIOS = [
    HarnessScenario(
        name="eye_exam_report",
        domain=OntologyDomain.MEDICAL,
        strategy="consensus",
        task="당뇨망막병증(H36.0) 환자 P123456의 안저 검사 소견을 "
             "간단히 작성하세요. 개인정보는 포함하지 마세요.",
        validators=[has_content, no_pii_data],
        expect_pass=True,
        tags=["medical", "report", "consensus"],
        timeout_sec=300,
        max_iterations=1,
    ),
    HarnessScenario(
        name="no_pii_check",
        domain=OntologyDomain.MEDICAL,
        strategy="pipeline",
        task="황반변성(H35.3) 환자의 OCT 검사 결과 요약을 작성하세요. "
             "환자 이름, 주민번호 등 개인정보는 절대 포함하지 마세요.",
        validators=[has_content, no_pii_data],
        expect_pass=True,
        tags=["medical", "pii", "safety"],
        timeout_sec=240,
        max_iterations=1,
    ),
]

# ── BUSINESS 시나리오 ─────────────────────────────────────
BUSINESS_SCENARIOS = [
    HarnessScenario(
        name="contract_summary",
        domain=OntologyDomain.BUSINESS,
        strategy="debate",
        task="소프트웨어 개발 용역 계약의 주요 검토 항목 5가지를 "
             "간단히 정리해 주세요.",
        validators=[has_content, has_business_content],
        expect_pass=True,
        tags=["business", "contract", "debate"],
        timeout_sec=300,
        max_iterations=2,
    ),
]

# ── 전체 시나리오 모음 ─────────────────────────────────────
ALL_SCENARIOS = SOFTWARE_SCENARIOS + MEDICAL_SCENARIOS + BUSINESS_SCENARIOS

# ── 스모크 테스트 (빠른 검증용) ───────────────────────────
SMOKE_SCENARIOS = [s for s in ALL_SCENARIOS if "smoke" in s.tags]

# ── 도메인별 조회 ─────────────────────────────────────────
def get_scenarios(
    domain: OntologyDomain | None = None,
    tags:   list[str] | None = None,
) -> list[HarnessScenario]:
    scenarios = ALL_SCENARIOS
    if domain:
        scenarios = [s for s in scenarios if s.domain == domain]
    if tags:
        scenarios = [s for s in scenarios if any(t in s.tags for t in tags)]
    return scenarios
