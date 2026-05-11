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
import re
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
    # domain=SVG 이고 에이전트 출력이 str일 때 for_svg()에 넣을 svg_type (Ontology enum 값)
    svg_ontology_type: str | None = None
    # domain=POLYGLOT 이고 출력이 코드 문자열일 때 for_polyglot(language) 검증 대상 언어
    polyglot_language: str | None = None


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
    pii_keywords = [
        "ssn",
        "주민번호",
        "social_security",
        "password",
        "passwd",
        "이름",
    ]
    found = [k for k in pii_keywords if k in text]
    ok = len(found) == 0
    return ok, "PII 없음" if ok else f"PII 발견: {found}"


def no_id_number_literals(output: Any) -> tuple[bool, str]:
    """
    코드/보고서에 실제 식별 번호 나열이 없는지 (교육 문구의 단어 '이름' 등은 제외).
    Harness security 시나리오용.
    """
    t = str(output)
    if re.search(r"\d{6}[- ]?\d{7}", t):
        return False, "주민번호 형식 숫자열"
    if re.search(r"\b\d{3}[- ]\d{2}[- ]\d{4}\b", t):
        return False, "SSN 형식 숫자열"
    if re.search(r"(주민|resident|ssn)\s*[:=]\s*[\d\-\s]{7,}", t, re.I):
        return False, "식별자 필드에 숫자열"
    return True, "리터럴 식별번호 없음"

def has_business_content(output: Any) -> tuple[bool, str]:
    """비즈니스 문서 내용 포함 여부"""
    text = str(output)
    ok = len(text) > 100
    return ok, f"비즈니스 내용 있음 ({len(text)}자)" if ok else "내용 너무 짧음"

def has_async_keyword(output: Any) -> tuple[bool, str]:
    """async def 키워드 포함 여부"""
    ok = "async def" in str(output)
    return ok, "async 함수 정의 있음" if ok else "async 키워드 없음"


def _extract_python_from_markdown(output: Any) -> str:
    """출력 문자열에서 ```python 블록이 있으면 추출. 없으면 전체 문자열 사용."""
    text = str(output).strip()
    fm = re.search(r"```(?:python|py)?\s*\n([\s\S]*?)```", text, re.I)
    if fm:
        return fm.group(1).strip()
    return text


def syntax_valid_python(output: Any) -> tuple[bool, str]:
    """추출한 파이썬 문자열이 ast.parse 에 통과하는지 (문법 오류 수정 시나리오용)."""
    import ast

    body = _extract_python_from_markdown(output)
    if not body:
        return False, "파이썬 코드 없음"
    try:
        ast.parse(body)
    except SyntaxError as e:
        return False, f"SyntaxError: {e.msg} (line {e.lineno})"
    return True, "파이썬 문법 검사 통과"


def has_dataclass_marker(output: Any) -> tuple[bool, str]:
    """@dataclass 데코레이터 포함 (클래스 생성 시나리오)."""
    t = str(output)
    ok = "@dataclass" in t
    return ok, "@dataclass 있음" if ok else "@dataclass 없음"


def has_class_keyword(output: Any) -> tuple[bool, str]:
    ok = "class " in str(output)
    return ok, "class 정의 있음" if ok else "class 정의 없음"


def has_fastapi_route(output: Any) -> tuple[bool, str]:
    """FastAPI 라우터/데코레이터 흔적 (엔드포인트 생성 시나리오)."""
    t = str(output)
    ok = (
        "APIRouter" in t
        or "@router." in t
        or "@app." in t
        or "fastapi." in t.lower()
        or "FastAPI(" in t
    )
    return ok, "FastAPI 라우트 흔적 있음" if ok else "FastAPI 라우트 흔적 없음"


def has_review_indicators(output: Any) -> tuple[bool, str]:
    """코드 리뷰 형태 출력(개선점·위험·권장 등) 간접 검증."""
    t = str(output).lower()
    keys = (
        "개선", "문제점", "권장", "리스크", "버그",
        "issue", "recommend", "improve", "complexity",
        "test", "readable", "naming", "smell",
        "복잡", "가독",
    )
    hit = [k for k in keys if k in t]
    ok = len(hit) >= 1
    return ok, f"리뷰 키워드: {hit[:4]}" if ok else "리뷰 키워드 부족"


def has_architecture_keywords(output: Any) -> tuple[bool, str]:
    """아키텍처 논의(모놀리식·MSA·모듈 경계 등) 흔적."""
    t = str(output).lower()
    keys = (
        "monolith", "microservice", "micro-service",
        "모놀리", "마이크로", "mvp", "service",
        "경계", "모듈", "decision", "트레이드",
    )
    hit = [k for k in keys if k in t]
    ok = len(hit) >= 1
    return ok, f"아키 키워드: {hit[:4]}" if ok else "아키텍처 키워드 부족"


def has_knowledge_keywords(output: Any) -> tuple[bool, str]:
    """RAG·임베딩·768차원 등 지식 도메인 키워드"""
    t = str(output).lower()
    keys = (
        "embedding", "vector", "768", "rag", "retrieval", "index",
        "similarity", "chunk", "search",
    )
    hit = [k for k in keys if k in t]
    ok = len(hit) >= 2
    return ok, f"지식 키워드: {hit[:6]}" if ok else "지식/RAG 키워드 부족"


def has_cost_keywords(output: Any) -> tuple[bool, str]:
    """예산·토큰·HEAVY/local 라우팅 등 비용 도메인 키워드"""
    t = str(output).lower()
    keys = (
        "budget", "token", "cost", "heavy", "local", "routing",
        "usd", "model", "complexity",
    )
    hit = [k for k in keys if k in t]
    ok = len(hit) >= 2
    return ok, f"비용 키워드: {hit[:6]}" if ok else "비용/라우팅 키워드 부족"


def extract_svg_fragment(output: Any) -> str:
    """에이전트 출력에서 첫 <svg …> … </svg> 블록 추출 (없으면 전체 문자열)."""
    text = str(output).strip()
    fm = re.search(r"<svg[\s\S]*?</svg>", text, re.I)
    if fm:
        return fm.group(0).strip()
    return text


def has_svg_root(output: Any) -> tuple[bool, str]:
    """출력에 유효한 SVG 루트 태그가 있는지 (간접 검증)."""
    frag = extract_svg_fragment(output)
    ok = "<svg" in frag.lower() and "</svg>" in frag.lower()
    return ok, "SVG 마크업 포함" if ok else "SVG 마크업 없음"


def has_viewbox_hint(output: Any) -> tuple[bool, str]:
    t = extract_svg_fragment(output).lower()
    ok = "viewbox=" in t or "viewBox=" in str(output)
    return ok, "viewBox 속성 있음" if ok else "viewBox 없음(권고)"


def extract_polyglot_code(output: Any, lang: str) -> str:
    """펜스 블록에서 언어별 코드 추출."""
    text = str(output).strip()
    langs: list[str] = []
    l = lang.lower().strip()
    if l == "typescript":
        langs = ["typescript", "ts"]
    elif l == "rust":
        langs = ["rust", "rs"]
    else:
        langs = ["python", "py"]
    for cand in langs:
        fm = re.search(
            rf"```(?:{re.escape(cand)})\s*\n([\s\S]*?)```",
            text,
            re.I,
        )
        if fm:
            return fm.group(1).strip()
    fb = re.search(r"```\s*\n([\s\S]*?)```", text)
    if fb:
        return fb.group(1).strip()
    return text


def infer_polyglot_function_name(code: str, lang: str) -> str:
    l = lang.lower().strip()
    if l == "typescript":
        m = re.search(
            r"(?:export\s+)?(?:async\s+)?function\s+([a-zA-Z_][a-zA-Z0-9]*)",
            code,
        )
        if m:
            return m.group(1)
        m = re.search(
            r"const\s+([a-z][a-zA-Z0-9]*)\s*=\s*(?:async\s*)?\(",
            code,
        )
        return m.group(1) if m else "snippet"
    if l == "rust":
        m = re.search(r"fn\s+([a-z_][a-z0-9_]*)", code)
        return m.group(1) if m else "snippet"
    m = re.search(r"def\s+([a-z_][a-z0-9_]*)", code)
    return m.group(1) if m else "snippet"


def triple_polyglot_fences(output: Any) -> tuple[bool, str]:
    """Python·TypeScript·Rust 펜스 3종 포함 여부 (polyglot_comparison)."""
    s = str(output).lower()
    ok_py = bool(re.search(r"```(?:python|py)\b", s))
    ok_ts = bool(re.search(r"```(?:typescript|ts)\b", s))
    ok_rs = bool(re.search(r"```(?:rust|rs)\b", s))
    ok = ok_py and ok_ts and ok_rs
    return ok, "3종 언어 펜스 감지" if ok else "python/typescript/rust 펜스 부족"


def has_medical_term(output: Any) -> tuple[bool, str]:
    """의학 용어(ICD 코드 또는 안과 용어) 포함 여부"""
    text = str(output).lower()
    terms = [
        "녹내장", "glaucoma", "황반", "macula", "망막", "retina",
        "시력", "visual acuity", "안압", "iop", "oct", "안저",
        "h40", "h35", "h36", "h26", "cornea", "각막",
    ]
    found = [t for t in terms if t in text]
    ok = len(found) >= 1
    return ok, f"의학 용어 있음: {found[:3]}" if ok else "의학 용어 없음"

def has_sufficient_length(output: Any) -> tuple[bool, str]:
    """출력이 충분히 긴지 확인 (200자 이상)"""
    text = str(output)
    ok = len(text) >= 200
    return ok, f"충분한 길이 ({len(text)}자)" if ok else f"내용 너무 짧음 ({len(text)}자, 200자 필요)"


def dashboard_medi_ontology_stats_api_ok(output: Any) -> tuple[bool, str]:
    """MEDI-API GET /api/v1/ontology/stats 필드 존재 확인 (Week 9 대시보드)."""
    import os

    import httpx

    url = os.environ.get(
        "HARNESS_MEDI_ONTOLOGY_STATS_URL",
        "http://medi-iot-api:8000/api/v1/ontology/stats",
    )
    try:
        r = httpx.get(url, timeout=25.0)
        if r.status_code != 200:
            body = r.text[:200]
            return False, f"MEDI ontology/stats HTTP {r.status_code}: {body}"
        data = r.json()
        for key in ("domain", "today_validations", "pass_rate", "top_errors"):
            if key not in data:
                return False, f"필수 필드 없음: {key}"
        if not isinstance(data["top_errors"], list):
            return False, "top_errors가 배열 아님"
        if isinstance(data["pass_rate"], bool) or not isinstance(
            data["pass_rate"], int | float
        ):
            return False, "pass_rate 타입 불일치"
        return True, "MEDI ontology stats JSON 스키마 OK"
    except Exception as e:
        return False, f"ontology/stats 검증 예외: {e!s}"[:220]


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
    HarnessScenario(
        name="fibonacci",
        domain=OntologyDomain.SOFTWARE,
        strategy="pipeline",
        task=(
            "n번째 피보나치 수를 반환하는 fibonacci(n: int) -> int 함수를 구현하세요. "
            "재귀와 반복(iterative) 두 가지 방식을 모두 구현하고, "
            "타입 힌트·docstring·에러 처리(n < 0 시 ValueError)를 포함하세요."
        ),
        validators=[has_content, has_def_keyword, has_type_hints, has_docstring],
        expect_pass=True,
        tags=["intermediate", "function", "algorithm"],
        timeout_sec=300,
        max_iterations=2,
    ),
    HarnessScenario(
        name="data_validator",
        domain=OntologyDomain.SOFTWARE,
        strategy="pipeline",
        task=(
            "이메일 주소와 한국 휴대폰 번호(010-XXXX-XXXX)를 각각 검증하는 "
            "validate_email(email: str) -> bool 과 "
            "validate_phone_kr(phone: str) -> bool 함수를 정규식을 사용하여 구현하세요. "
            "타입 힌트, docstring, 예시 테스트 코드를 포함하세요."
        ),
        validators=[has_content, has_def_keyword, has_type_hints, has_docstring],
        expect_pass=True,
        tags=["intermediate", "function", "validation", "regex"],
        timeout_sec=300,
        max_iterations=2,
    ),
    HarnessScenario(
        name="async_fetcher",
        domain=OntologyDomain.SOFTWARE,
        strategy="pipeline",
        task=(
            "aiohttp를 사용하여 여러 URL에서 비동기로 데이터를 가져오는 "
            "async def fetch_all(urls: list[str]) -> list[dict] 함수를 구현하세요. "
            "타임아웃(30초), 에러 처리, 타입 힌트, docstring을 포함하세요."
        ),
        validators=[has_content, has_def_keyword, has_type_hints, has_async_keyword],
        expect_pass=True,
        tags=["advanced", "function", "async", "networking"],
        timeout_sec=300,
        max_iterations=2,
    ),
    # ── AutoNoGaDa 전용 (WEEK4 4-4-1) ─────────────────────────
    HarnessScenario(
        name="generate_class",
        domain=OntologyDomain.SOFTWARE,
        strategy="pipeline",
        task=(
            "Python `@dataclass`와 `frozen=True`를 사용하는 `UserDTO` 클래스를 작성하세요. "
            "필드: `user_id: int`, `name: str`, `email: str | None = None`. "
            "`field`로 검증 또는 기본값 설명 주석은 선택. 클래스와 필드 타입만 명확히 하고 불필요한 보일러플레이트는 줄이세요. "
            "코드만 출력하세요."
        ),
        validators=[has_content, has_class_keyword, has_dataclass_marker, has_type_hints],
        expect_pass=True,
        tags=["autonogada", "class", "dataclass"],
        timeout_sec=300,
        max_iterations=2,
    ),
    HarnessScenario(
        name="generate_api_endpoint",
        domain=OntologyDomain.SOFTWARE,
        strategy="pipeline",
        task=(
            "FastAPI `APIRouter(prefix='/items')`를 사용하여 "
            "`GET /items/` 로 목록을 반환하고 `POST /items/` 로 항목을 생성하는 두 엔드포인트를 "
            "`async def`로 구현하세요. 간단히 Pydantic `BaseModel`(name: str)만 사용해도 됩니다. "
            "실제 저장소는 빈 리스트 메모리면 됩니다. 전체 라우터 정의 포함 코드만 출력하세요."
        ),
        validators=[has_content, has_fastapi_route, has_async_keyword, has_def_keyword],
        expect_pass=True,
        tags=["autonogada", "fastapi", "api"],
        timeout_sec=320,
        max_iterations=2,
    ),
    HarnessScenario(
        name="fix_syntax_error",
        domain=OntologyDomain.SOFTWARE,
        strategy="pipeline",
        task=(
            "다음 파이썬 코드에 문법 오류가 있습니다. **수정된 전체 코드**만 출력하세요.\n\n"
            "```python\n"
            "def broken_sum(a, b)\n"
            "    \"\"\"두 수를 더한다.\"\"\"\n"
            "    return a + b\n"
            "```"
        ),
        validators=[has_content, has_def_keyword, syntax_valid_python],
        expect_pass=True,
        tags=["autonogada", "fix", "syntax"],
        timeout_sec=240,
        max_iterations=2,
    ),
    HarnessScenario(
        name="review_complex_function",
        domain=OntologyDomain.SOFTWARE,
        strategy="pipeline",
        task=(
            "아래 함수에 대해 **코드 리뷰**만 작성하세요 (새 구현 코드를 다시 작성하지 마세요).\n\n"
            "```python\n"
            "def f(n):\n"
            "    r = []; i = j = k = n\n"
            "    while i>0:j=i*i;k=j+i;i-=1;r.append((j,k))\n"
            "    def g(x):\n"
            "        if x:r.extend(g(x-1))\n"
            "    g(3); return \"\".join(str(t)for t in r)\n"
            "```\n\n"
            "가독성, 네이밍, 테스트 권장, 잠재 버그 또는 복잡도를 불릿/짧은 문단으로 논하고, "
            "**개선·문제점·권장** 단어 또는 영어 등가 표현을 반드시 포함하세요."
        ),
        validators=[has_content, has_review_indicators, has_sufficient_length],
        expect_pass=True,
        tags=["autonogada", "review"],
        timeout_sec=300,
        max_iterations=2,
    ),
    HarnessScenario(
        name="debate_architecture",
        domain=OntologyDomain.SOFTWARE,
        strategy="debate",
        task=(
            "초기 5명 이하 스타트업이 MVP 백엔드를 만든다. "
            "**모놀리식(Monolithic)** 과 **마이크로서비스(Microservices)** 중 무엇을 권하고 왜 그런지 논하고, "
            "트레이드오프와 나중에 전환 가능성을 포함하세요. "
            "**MVP**, **마이크로** 또는 **microservice**, **모놀리** 또는 **monolith** 같은 용어를 본문에 포함하세요. "
            "한국어로 답해도 됩니다."
        ),
        validators=[has_content, has_architecture_keywords, has_sufficient_length],
        expect_pass=True,
        tags=["autonogada", "debate", "architecture"],
        timeout_sec=400,
        max_iterations=2,
    ),
    HarnessScenario(
        name="dashboard_api_ontology_stats",
        domain=OntologyDomain.SOFTWARE,
        strategy="fastest",
        task="한 단어만 출력하세요: OK",
        validators=[has_content, dashboard_medi_ontology_stats_api_ok],
        expect_pass=True,
        tags=["dashboard", "smoke", "api"],
        timeout_sec=90,
        max_iterations=1,
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
    HarnessScenario(
        name="glaucoma_report",
        domain=OntologyDomain.MEDICAL,
        strategy="consensus",
        task=(
            "개방각 녹내장(H40.1) 환자의 시야 검사(Visual Field Test) 소견을 작성하세요. "
            "안압 수치, 시신경 손상 단계(초기/중기/말기), 치료 방향을 포함하되 "
            "환자 개인정보(이름, 주민번호 등)는 포함하지 마세요."
        ),
        validators=[has_content, no_pii_data, has_medical_term, has_sufficient_length],
        expect_pass=True,
        tags=["medical", "eye", "glaucoma", "report", "consensus"],
        timeout_sec=300,
        max_iterations=1,
    ),
    HarnessScenario(
        name="vision_correction",
        domain=OntologyDomain.MEDICAL,
        strategy="pipeline",
        task=(
            "라식(LASIK) 수술 전 기본 검사 항목 체크리스트를 작성하세요. "
            "각막 두께, 굴절 이상 범위, 금기 사항(원추각막 등)을 포함한 "
            "5~7개 항목을 의학적으로 정확하게 기술하세요. "
            "개인정보는 포함하지 마세요."
        ),
        validators=[has_content, no_pii_data, has_medical_term, has_sufficient_length],
        expect_pass=True,
        tags=["medical", "eye", "vision-correction", "checklist"],
        timeout_sec=240,
        max_iterations=1,
    ),
    HarnessScenario(
        name="oct_analysis",
        domain=OntologyDomain.MEDICAL,
        strategy="consensus",
        task=(
            "빛간섭단층촬영(OCT) 검사에서 황반원공(Macular Hole, H35.34)이 "
            "발견된 경우의 소견 및 처치 방향을 간략히 서술하세요. "
            "OCT 소견 특징(층 구조 손상 등), 수술 필요성 판단 기준을 포함하세요. "
            "개인식별정보는 포함하지 마세요."
        ),
        validators=[has_content, no_pii_data, has_medical_term, has_sufficient_length],
        expect_pass=True,
        tags=["medical", "eye", "oct", "report", "consensus"],
        timeout_sec=300,
        max_iterations=1,
    ),
    HarnessScenario(
        name="vision_medical_report",
        domain=OntologyDomain.MEDICAL,
        strategy="consensus",
        task=(
            "안저 이미지 소견: 후극부 점상 출혈, 황반부 경성삼출물 관찰. "
            "ICD-10 진단 코드와 치료 권고사항을 포함한 의료 보고서를 작성하세요. "
            "개인정보는 절대 포함하지 마세요."
        ),
        validators=[
            has_content, no_pii_data, has_medical_term, has_sufficient_length,
        ],
        expect_pass=True,
        tags=["medical", "vision", "icd10", "consensus"],
        timeout_sec=200,
        max_iterations=1,
    ),
    HarnessScenario(
        name="security_pii_protection",
        domain=OntologyDomain.MEDICAL,
        strategy="consensus",
        task=(
            "환자 데이터를 처리하는 함수를 구현하되 주민번호, SSN, 이름 등 "
            "개인식별정보가 절대 포함되지 않도록 구현하세요."
        ),
        validators=[has_content, no_id_number_literals, has_def_keyword],
        expect_pass=True,
        tags=["security", "pii", "medical", "compliance"],
        timeout_sec=150,
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
    HarnessScenario(
        name="contract_risk_analysis",
        domain=OntologyDomain.BUSINESS,
        strategy="debate",
        task=(
            "소프트웨어 개발 용역 계약서를 검토하고 위험 수준(low/medium/high)과 "
            "주요 위험 항목 3가지를 CON-20260508 형식 계약번호와 함께 제시하세요."
        ),
        validators=[has_content, has_business_content, has_sufficient_length],
        expect_pass=True,
        tags=["business", "contract", "debate", "risk"],
        timeout_sec=200,
        max_iterations=2,
    ),
    HarnessScenario(
        name="approval_ontology_check",
        domain=OntologyDomain.BUSINESS,
        strategy="pipeline",
        task=(
            "금액 5000만원, 통화 KRW의 IT 서비스 계약에 대한 결재 요청서를 작성하세요. "
            "승인자 ID와 결재 사유를 반드시 포함하세요."
        ),
        validators=[has_content, has_business_content],
        expect_pass=True,
        tags=["business", "approval", "ontology"],
        timeout_sec=150,
        max_iterations=2,
    ),
    HarnessScenario(
        name="security_policy_enforcement",
        domain=OntologyDomain.BUSINESS,
        strategy="pipeline",
        task=(
            "역할 기반 접근 제어(RBAC) 정책을 검토하고 "
            "doctor 역할이 ai_analyze 권한만 가지도록 "
            "정책 문서를 작성하세요."
        ),
        validators=[has_content, has_business_content],
        expect_pass=True,
        tags=["security", "rbac", "policy"],
        timeout_sec=120,
        max_iterations=2,
    ),
]

# ── Phase 2: KNOWLEDGE / COST (OntologyDomain 확장) ────────
KNOWLEDGE_SCENARIOS = [
    HarnessScenario(
        name="knowledge_embedding_schema",
        domain=OntologyDomain.KNOWLEDGE,
        strategy="pipeline",
        task=(
            "문서 검색용 RAG 파이프라인을 한국어로 설명하세요. "
            "768차원 embedding·vector 인덱싱·유사도 검색 개념을 본문에 포함하고, "
            "PII는 넣지 마세요."
        ),
        validators=[has_content, has_knowledge_keywords, has_sufficient_length],
        expect_pass=True,
        tags=["phase2", "knowledge", "rag", "embedding"],
        timeout_sec=280,
        max_iterations=1,
    ),
    HarnessScenario(
        name="knowledge_task_indexing",
        domain=OntologyDomain.KNOWLEDGE,
        strategy="pipeline",
        task=(
            "코드베이스 검색 에이전트용 'task' 메타데이터(어느 저장소/브랜치인지)와 "
            "chunking·retrieval·768 차원 임베딩 전략을 요약하세요. "
            "768, embedding, RAG 단어를 반드시 포함하세요."
        ),
        validators=[has_content, has_knowledge_keywords],
        expect_pass=True,
        tags=["phase2", "knowledge", "indexing"],
        timeout_sec=300,
        max_iterations=1,
    ),
]

COST_SCENARIOS = [
    HarnessScenario(
        name="cost_routing_heavy_budget",
        domain=OntologyDomain.COST,
        strategy="pipeline",
        task=(
            "복잡도가 critical일 때 HEAVY 또는 CONSENSUS 모델을 쓰는 규칙과, "
            "budget_usd가 극소($0.01 미만)일 때 local/fast 모델만 쓰는 라우팅 정책을 "
            "한국어로 간단히 서술하세요. budget, token, HEAVY, local 단어를 포함하세요."
        ),
        validators=[has_content, has_cost_keywords, has_sufficient_length],
        expect_pass=True,
        tags=["phase2", "cost", "routing"],
        timeout_sec=260,
        max_iterations=1,
    ),
    HarnessScenario(
        name="cost_micro_budget_policy",
        domain=OntologyDomain.COST,
        strategy="debate",
        task=(
            "예산이 $0.005 수준일 때 클라우드 API 대신 로컬(LM Studio) 모델만 쓰는 것이 "
            "합리적인지 토론하세요. 비용·토큰·지연 시간을 언급하고 "
            "budget와 local 키워드를 본문에 넣으세요."
        ),
        validators=[has_content, has_cost_keywords],
        expect_pass=True,
        tags=["phase2", "cost", "debate", "local"],
        timeout_sec=340,
        max_iterations=2,
    ),
]

# ── Phase 2: POLYGLOT (Harness — for_polyglot(str) 코드 추출 검증) ──
POLYGLOT_SCENARIOS = [
    HarnessScenario(
        name="typescript_function",
        domain=OntologyDomain.POLYGLOT,
        strategy="pipeline",
        task=(
            "**TypeScript** 로 두 문자열 길이의 합을 반환하는 `stringLengthSum` "
            "camelCase 함수 하나만 작성하세요. `any` 타입 사용 금지. "
            "코드는 ```typescript 펜스로 감싸세요."
        ),
        validators=[has_content],
        expect_pass=True,
        tags=["phase2", "polyglot", "typescript"],
        timeout_sec=300,
        max_iterations=2,
        polyglot_language="typescript",
    ),
    HarnessScenario(
        name="rust_function",
        domain=OntologyDomain.POLYGLOT,
        strategy="pipeline",
        task=(
            "**Rust** 로 `fn clamp_value(x: i32, lo: i32, hi: i32) -> i32` 구현 하나만 작성. "
            "`.unwrap()` 은 필요 시에만 최소 사용(4회 이상 금지). ```rust 블록으로 출력."
        ),
        validators=[has_content],
        expect_pass=True,
        tags=["phase2", "polyglot", "rust"],
        timeout_sec=320,
        max_iterations=2,
        polyglot_language="rust",
    ),
    HarnessScenario(
        name="polyglot_comparison",
        domain=OntologyDomain.POLYGLOT,
        strategy="pipeline",
        task=(
            "동일한 로직(**정수 절대값**)을 각각 한 함수로 작성: "
            "Python에는 `abs_int`(snake_case), TypeScript에는 `absInt`(camelCase), Rust에는 "
            "`abs_int`(snake_case) 함수만 넣습니다. 출력은 세 개의 블록 "
            "```python ```typescript ```rust 로만 구성합니다(자연어 문장 불필요)."
        ),
        validators=[has_content, triple_polyglot_fences],
        expect_pass=True,
        tags=["phase2", "polyglot", "comparison"],
        timeout_sec=400,
        max_iterations=2,
        polyglot_language=None,
    ),
]

# ── Phase 2: SVG (Harness — str 출력에 대해 for_svg 검증) ──
SVG_SCENARIOS = [
    HarnessScenario(
        name="svg_architecture_three_tier",
        domain=OntologyDomain.SVG,
        strategy="pipeline",
        task=(
            "다음 조건을 만족하는 **SVG XML만** 출력하세요. 자연어 설명은 하지 마세요.\n"
            "- xmlns='http://www.w3.org/2000/svg', viewBox='0 0 520 200', width/height 양수\n"
            "- Presentation / Application / Data 3개 레이어를 rect와 text로 표현\n"
            "- <script>, http://, https:// 링크 금지\n"
        ),
        validators=[has_content, has_svg_root, has_viewbox_hint],
        expect_pass=True,
        tags=["phase2", "svg", "architecture"],
        timeout_sec=300,
        max_iterations=2,
        svg_ontology_type="architecture",
    ),
    HarnessScenario(
        name="svg_flowchart_decision_stub",
        domain=OntologyDomain.SVG,
        strategy="pipeline",
        task=(
            "**SVG XML만** 출력하세요. 간단한 결정 다이어그램(마름모 1개 + Yes/No 박스)을 "
            "viewBox가 있는 단일 <svg>로 그리세요. script 태그 금지."
        ),
        validators=[has_content, has_svg_root],
        expect_pass=True,
        tags=["phase2", "svg", "flowchart"],
        timeout_sec=280,
        max_iterations=2,
        svg_ontology_type="flowchart",
    ),
    HarnessScenario(
        name="svg_flowchart_generation",
        domain=OntologyDomain.SVG,
        strategy="fastest",
        task=(
            "사용자 로그인 → JWT 발급 → API 접근 흐름을 플로우차트로 그려주세요. "
            "**SVG XML만** 출력하세요 (자연어 설명 금지)."
        ),
        validators=[has_content],
        expect_pass=True,
        tags=["svg", "flowchart", "smoke"],
        timeout_sec=120,
        max_iterations=1,
        svg_ontology_type="flowchart",
    ),
    HarnessScenario(
        name="svg_medical_report_no_pii",
        domain=OntologyDomain.SVG,
        strategy="pipeline",
        task=(
            "황반변성(H35.3) 진단 결과를 의료 보고서 형태의 **SVG XML만** 생성하세요. "
            "차트·도식 영역 외에는 설명을 쓰지 마세요. "
            "환자 이름, 주민번호 등 개인정보는 절대 포함하지 마세요."
        ),
        validators=[has_content, no_pii_data],
        expect_pass=True,
        tags=["svg", "medical", "pii"],
        timeout_sec=150,
        max_iterations=1,
        svg_ontology_type="medical_report",
    ),
]

# ── 전체 시나리오 모음 ─────────────────────────────────────
ALL_SCENARIOS = (
    SOFTWARE_SCENARIOS
    + MEDICAL_SCENARIOS
    + BUSINESS_SCENARIOS
    + KNOWLEDGE_SCENARIOS
    + COST_SCENARIOS
    + POLYGLOT_SCENARIOS
    + SVG_SCENARIOS
)

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
