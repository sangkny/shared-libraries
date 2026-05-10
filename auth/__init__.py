"""JWT 기반 인증 (Week 7) — 패키지 루트 `auth`."""

from .dependencies import optional_user, require_role
from .jwt_handler import create_access_token, verify_token_payload
from .routes import router as auth_router

__all__ = [
    "auth_router",
    "create_access_token",
    "verify_token_payload",
    "optional_user",
    "require_role",
]
