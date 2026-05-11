"""JWT 기반 인증 (Week 7) + OAuth2 / 정책 (Week 11) — 패키지 루트 `auth`."""

from .dependencies import optional_user, require_role
from .jwt_handler import (
    create_access_token,
    create_refresh_token,
    verify_token_payload,
    verify_refresh_payload,
)
from .policy import PolicyEngine, POLICIES, policy_require
from .routes import router as auth_router

__all__ = [
    "auth_router",
    "create_access_token",
    "create_refresh_token",
    "verify_token_payload",
    "verify_refresh_payload",
    "optional_user",
    "require_role",
    "PolicyEngine",
    "POLICIES",
    "policy_require",
]
