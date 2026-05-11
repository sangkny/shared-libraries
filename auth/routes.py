"""로그인 / OAuth2 / 토큰 갱신 (Phase 2 Week 11)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field

from .dependencies import verify_dev_password
from .jwt_handler import (
    create_access_token,
    verify_refresh_payload,
    revoke_refresh_token,
    is_refresh_revoked,
)
from .oauth2 import exchange_github_code, exchange_google_code

router = APIRouter()


@router.post("/token")
async def login(form: OAuth2PasswordRequestForm = Depends()) -> dict[str, str]:
    role = verify_dev_password(form.username, form.password)
    token = create_access_token(subject=form.username, role=role)
    return {"access_token": token, "token_type": "bearer"}


class OAuthCodeBody(BaseModel):
    code: str = Field(..., min_length=1, max_length=4000)
    redirect_uri: str | None = Field(default=None, max_length=2000)


@router.post("/oauth/google")
async def oauth_google(body: OAuthCodeBody) -> dict:
    return await exchange_google_code(body.code, body.redirect_uri)


@router.post("/oauth/github")
async def oauth_github(body: OAuthCodeBody) -> dict:
    return await exchange_github_code(body.code, body.redirect_uri)


class RefreshBody(BaseModel):
    refresh_token: str = Field(..., min_length=20)


@router.post("/refresh")
async def auth_refresh(body: RefreshBody) -> dict[str, str]:
    if is_refresh_revoked(body.refresh_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="revoked")
    try:
        p = verify_refresh_payload(body.refresh_token)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        ) from e
    sub = str(p.get("sub", ""))
    role = str(p.get("role", ""))
    if not sub or not role:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid")
    access = create_access_token(subject=sub, role=role)
    return {"access_token": access, "token_type": "bearer"}


class LogoutBody(BaseModel):
    refresh_token: str | None = None


@router.post("/logout")
async def auth_logout(body: LogoutBody) -> dict[str, str]:
    """클라이언트가 access 폐기 후 refresh 무효화 권장."""
    if body.refresh_token:
        revoke_refresh_token(body.refresh_token)
    return {"status": "ok"}
