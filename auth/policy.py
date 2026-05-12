"""정책 기반 접근 제어(RBAC) — Phase 2 Week 11."""
from __future__ import annotations

from collections.abc import Callable
from typing import Final

from fastapi import Depends, HTTPException, status

from .dependencies import current_user_strict

POLICIES: Final[dict[str, dict[str, list[str]]]] = {
    "medi-iot": {
        "doctor": [
            "read", "create_exam", "ai_analyze", "upload_image",
            "promote_diagnosis", "review_diagnosis",
        ],
        "staff": ["read", "create_patient"],
        "admin": ["*"],
    },
    "autonogada": {
        "developer": ["generate", "review", "fix", "svg", "architecture"],
        "admin": ["*"],
    },
    "coops": {
        "staff": ["request_approval", "read", "create_contract"],
        "manager": ["approve", "reject", "read", "analyze_contract"],
        "admin": ["*"],
    },
}


class PolicyEngine:
    """플랫폼별 역할·액션 허용 여부."""

    def __init__(self, policies: dict[str, dict[str, list[str]]] | None = None) -> None:
        self._policies = policies or POLICIES

    def check(self, platform: str, role: str, action: str) -> bool:
        plat = self._policies.get(platform)
        if not plat:
            return False
        perms = plat.get(role)
        if not perms:
            return False
        if "*" in perms:
            return True
        return action in perms


default_engine = PolicyEngine()


def policy_require(platform: str, action: str) -> Callable[..., dict[str, str]]:
    """FastAPI Depends: JWT 사용자 + PolicyEngine.check."""

    async def _dep(user: dict[str, str] = Depends(current_user_strict)) -> dict[str, str]:
        r = str(user.get("role", ""))
        if not default_engine.check(platform, r, action):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"정책 거부: platform={platform} action={action} role={r}",
            )
        return user

    return _dep
