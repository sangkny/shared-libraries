"""Phase 2 선행 — OntologyDomain SVG/POLYGLOT/KNOWLEDGE/COST 회귀."""
from __future__ import annotations

import pytest

from ontology.base import OntologyDomain
from ontology.validator import OntologyValidator


@pytest.mark.parametrize(
    ("member", "value"),
    [
        ("SVG", "svg"),
        ("POLYGLOT", "polyglot"),
        ("KNOWLEDGE", "knowledge"),
        ("COST", "cost"),
    ],
)
def test_phase2_enum_members(member: str, value: str) -> None:
    assert getattr(OntologyDomain, member).value == value


@pytest.mark.asyncio
async def test_for_domain_uses_phase2_default_rules() -> None:
    """strict 도메인은 빈 페이로드가 실패하고, 매핑은 유지된다."""
    v_svg = OntologyValidator.for_domain("svg")
    assert v_svg.domain.value == "svg"
    r_empty = await v_svg.validate({})
    assert r_empty.passed is False

    v_k = OntologyValidator.for_knowledge()
    ok = {
        "task": "1234567890열두자이상태스크",
        "language": "python",
        "result": "x",
        "success": True,
    }
    assert (await v_k.validate(ok)).passed is True
