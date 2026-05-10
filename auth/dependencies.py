"""FastAPI 의존성 — Bearer JWT."""
from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .jwt_handler import verify_token_payload

http_bearer = HTTPBearer(auto_error=False)

# 개발 하드코딩 계정: (password, role)
DEV_USERS: dict[str, tuple[str, str]] = {
    "admin":    ("admin123", "admin"),
    "doctor":   ("doc123", "doctor"),
    "staff":    ("staff123", "staff"),
    "developer": ("dev123", "developer"),
}


def verify_dev_password(username: str, password: str) -> str:
    """검증 시 role 반환."""
    row = DEV_USERS.get(username)
    if not row or row[0] != password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="잘못된 사용자 이름 또는 비밀번호입니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return row[1]


async def optional_user(
    creds: HTTPAuthorizationCredentials | None = Depends(http_bearer),
) -> dict[str, str] | None:
    if creds is None or creds.credentials.strip() == "":
        return None
    try:
        payload = verify_token_payload(creds.credentials)
        return {
            "user_id": str(payload.get("sub", "")),
            "role":    str(payload.get("role", "")),
        }
    except ValueError:
        return None


def require_role(*allowed_roles: str) -> Callable[..., dict[str, str]]:
    roles_set = set(allowed_roles)

    async def _dep(user: dict | None = Depends(optional_user)) -> dict[str, str]:
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="인증이 필요합니다.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if user["role"] not in roles_set:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"허용되지 않은 역할: {user['role']} (허용: {sorted(roles_set)})",
            )
        return user

    return _dep


async def current_user_strict(
    creds: HTTPAuthorizationCredentials | None = Depends(http_bearer),
) -> dict[str, str]:
    """Authorization 헤더 필수."""
    if creds is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer 토큰이 필요합니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = verify_token_payload(creds.credentials)
        return {"user_id": str(payload["sub"]), "role": str(payload["role"])}
    except (ValueError, KeyError) as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"토큰이 유효하지 않습니다: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e


def strict_require_role(*allowed_roles: str) -> Callable[..., dict[str, str]]:
    roles_set = set(allowed_roles)

    async def _dep(user: dict[str, str] = Depends(current_user_strict)) -> dict[str, str]:
        if user["role"] not in roles_set:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"허용되지 않은 역할입니다.",
            )
        return user

    return _dep
