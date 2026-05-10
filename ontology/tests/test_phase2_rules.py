# Phase 2 Ontology 규칙 — SVG / POLYGLOT / KNOWLEDGE / COST
from __future__ import annotations

import pytest

from ontology.validator import OntologyValidator


def _svg_ok() -> str:
    """512KB 미만, 요소 1000 이하, script 없음"""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10" '
        'width="10" height="10"><rect width="10" height="10"/></svg>'
    )


# ── Step 2: SVG ───────────────────────────────────────────
class TestPhase2Svg:
    @pytest.mark.asyncio
    async def test_svg_valid(self) -> None:
        v = OntologyValidator.for_svg()
        data = {
            "svg_content": _svg_ok(),
            "svg_type": "flowchart",
        }
        r = await v.validate(data)
        assert r.passed is True

    @pytest.mark.asyncio
    async def test_svg_xss_fails(self) -> None:
        v = OntologyValidator.for_svg()
        bad = _svg_ok().replace(
            "<rect", "<script>alert(1)</script><rect", 1
        )
        data = {"svg_content": bad, "svg_type": "flowchart"}
        r = await v.validate(data)
        assert r.passed is False
        assert any(e.code == "SVG-SEM-XSS" for e in r.errors)

    @pytest.mark.asyncio
    async def test_svg_byte_limit(self) -> None:
        v = OntologyValidator.for_svg()
        huge = "x" * (512_000 + 1)
        data = {"svg_content": huge, "svg_type": "flowchart"}
        r = await v.validate(data)
        assert r.passed is False
        assert any(e.code == "CON-008" for e in r.errors)

    @pytest.mark.asyncio
    async def test_medical_report_requires_no_pii_true(self) -> None:
        v = OntologyValidator.for_svg()
        data = {
            "svg_content": _svg_ok(),
            "svg_type": "medical_report",
            "no_pii": False,
        }
        r = await v.validate(data)
        assert r.passed is False
        assert any(e.code == "DEP-001" and e.field == "no_pii" for e in r.errors)

    @pytest.mark.asyncio
    async def test_medical_report_pii_pattern(self) -> None:
        v = OntologyValidator.for_svg()
        bad = _svg_ok().replace("</svg>", " text 900101-1234567 </svg>", 1)
        data = {
            "svg_content": bad,
            "svg_type": "medical_report",
            "no_pii": True,
        }
        r = await v.validate(data)
        assert r.passed is False
        assert any(e.code == "SVG-SEM-PII" for e in r.errors)


# ── Step 3: POLYGLOT ─────────────────────────────────────
class TestPhase2Polyglot:
    @pytest.mark.asyncio
    async def test_python_ok(self) -> None:
        v = OntologyValidator.for_polyglot("python")
        data = {
            "code": "def add(a: int, b: int) -> int:\n    return a + b\n",
            "function_name": "add",
        }
        r = await v.validate(data)
        assert r.passed is True

    @pytest.mark.asyncio
    async def test_python_eval_forbidden(self) -> None:
        v = OntologyValidator.for_polyglot("python")
        data = {"code": "eval('1+1')"}
        r = await v.validate(data)
        assert r.passed is False
        assert any(e.code == "POLY-SEM-001" for e in r.errors)

    @pytest.mark.asyncio
    async def test_typescript_any_forbidden(self) -> None:
        v = OntologyValidator.for_polyglot("typescript")
        data = {"code": "function f(x: any): number { return 1; }"}
        r = await v.validate(data)
        assert r.passed is False
        assert any(e.code == "POLY-SEM-011" for e in r.errors)

    @pytest.mark.asyncio
    async def test_rust_unwrap_limit(self) -> None:
        v = OntologyValidator.for_polyglot("rust")
        unwraps = "\n".join("let _ = x.unwrap();" for _ in range(5))
        data = {"code": f"fn demo() {{\n{unwraps}\n}}"}
        r = await v.validate(data)
        assert r.passed is False
        assert any(e.code == "POLY-SEM-021" for e in r.errors)

    def test_polyglot_bad_language(self) -> None:
        with pytest.raises(ValueError, match="unsupported polyglot"):
            OntologyValidator.for_polyglot("go")


# ── Step 4: KNOWLEDGE ────────────────────────────────────
class TestPhase2Knowledge:
    @pytest.mark.asyncio
    async def test_knowledge_ok_with_embedding(self) -> None:
        v = OntologyValidator.for_knowledge()
        data = {
            "task": "10자이상태스크입니다임베딩검증",
            "language": "python",
            "result": "def f(): pass",
            "success": True,
            "embedding": [0.0] * 768,
        }
        r = await v.validate(data)
        assert r.passed is True

    @pytest.mark.asyncio
    async def test_knowledge_fail_wrong_embedding_dim(self) -> None:
        v = OntologyValidator.for_knowledge()
        data = {
            "task": "10자이상테스트작업입니다지식스키마",
            "language": "rust",
            "result": "ok",
            "success": True,
            "embedding": [0.0] * 100,
        }
        r = await v.validate(data)
        assert r.passed is False
        assert any(e.code == "CON-009" for e in r.errors)

    @pytest.mark.asyncio
    async def test_knowledge_fail_success_false_no_error_message(self) -> None:
        v = OntologyValidator.for_knowledge()
        data = {
            "task": "10자이상실패케이스검증입니다error_message필수",
            "language": "python",
            "result": "",
            "success": False,
        }
        r = await v.validate(data)
        assert r.passed is False
        assert any(e.field == "error_message" for e in r.errors)


# ── Step 5: COST ─────────────────────────────────────────
class TestPhase2Cost:
    @pytest.mark.asyncio
    async def test_cost_critical_requires_heavy(self) -> None:
        v = OntologyValidator.for_cost()
        data = {
            "task": "audit",
            "complexity": "critical",
            "selected_model": "google/gemma-4-e4b",
            "estimated_tokens": 1000,
        }
        r = await v.validate(data)
        assert r.passed is False
        assert any(e.code == "COST-DEP-001" for e in r.errors)

    @pytest.mark.asyncio
    async def test_cost_critical_heavy_ok(self) -> None:
        v = OntologyValidator.for_cost()
        data = {
            "task": "audit",
            "complexity": "critical",
            "selected_model": "HEAVY/gemma-4-26b",
            "estimated_tokens": 1000,
        }
        r = await v.validate(data)
        assert r.passed is True

    @pytest.mark.asyncio
    async def test_cost_micro_budget_local(self) -> None:
        v = OntologyValidator.for_cost()
        data = {
            "task": "tiny",
            "complexity": "simple",
            "selected_model": "openai/gpt-4",
            "estimated_tokens": 50,
            "budget_usd": 0.005,
        }
        r = await v.validate(data)
        assert r.passed is False
        assert any(e.code == "COST-DEP-002" for e in r.errors)

    @pytest.mark.asyncio
    async def test_cost_micro_budget_local_ok(self) -> None:
        v = OntologyValidator.for_cost()
        data = {
            "task": "tiny",
            "complexity": "simple",
            "selected_model": "lm-studio LOCAL_FAST gemma-4-e4b",
            "estimated_tokens": 50,
            "budget_usd": 0.005,
        }
        r = await v.validate(data)
        assert r.passed is True
