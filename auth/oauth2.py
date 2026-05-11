"""Google / GitHub OAuth2 코드 교환 — Phase 2 Week 11 (Mock 지원)."""
from __future__ import annotations

import os
from typing import Any

import httpx

from .jwt_handler import create_access_token, create_refresh_token

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"


def _default_oauth_role() -> str:
    return (os.getenv("OAUTH_DEFAULT_ROLE", "developer") or "developer").strip()


def _mock_tokens(*, subject: str, role: str | None = None) -> dict[str, Any]:
    r = role or _default_oauth_role()
    access = create_access_token(subject=subject, role=r)
    refresh = create_refresh_token(subject=subject, role=r)
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": 3600,
        "role": r,
    }


async def exchange_google_code(code: str, redirect_uri: str | None) -> dict[str, Any]:
    cid = (os.getenv("GOOGLE_CLIENT_ID") or "").strip()
    sec = (os.getenv("GOOGLE_CLIENT_SECRET") or "").strip()
    if (not cid or not sec) or code.strip().startswith("mock_"):
        return _mock_tokens(subject=f"google:{code[:48]}", role=_default_oauth_role())

    data = {
        "code": code,
        "client_id": cid,
        "client_secret": sec,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri or "http://localhost:8000/auth/callback/google",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        tr = await client.post(GOOGLE_TOKEN_URL, data=data)
        tr.raise_for_status()
        tok = tr.json()
        access = str(tok.get("access_token") or "")
        if not access:
            return _mock_tokens(subject="google:unknown")
        ur = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access}"},
        )
        ur.raise_for_status()
        profile = ur.json()
        sub = str(profile.get("email") or profile.get("sub") or "google-user")
        return _mock_tokens(subject=sub, role=_default_oauth_role())


async def exchange_github_code(code: str, redirect_uri: str | None) -> dict[str, Any]:
    cid = (os.getenv("GITHUB_CLIENT_ID") or "").strip()
    sec = (os.getenv("GITHUB_CLIENT_SECRET") or "").strip()
    if (not cid or not sec) or code.strip().startswith("mock_"):
        return _mock_tokens(subject=f"github:{code[:48]}", role=_default_oauth_role())

    async with httpx.AsyncClient(timeout=30.0) as client:
        tr = await client.post(
            GITHUB_TOKEN_URL,
            data={
                "code": code,
                "client_id": cid,
                "client_secret": sec,
                "redirect_uri": redirect_uri or "http://localhost:8000/auth/callback/github",
            },
            headers={"Accept": "application/json"},
        )
        tr.raise_for_status()
        tok = tr.json()
        access = str(tok.get("access_token") or "")
        if not access:
            return _mock_tokens(subject="github:unknown")
        ur = await client.get(
            GITHUB_USER_URL,
            headers={
                "Authorization": f"Bearer {access}",
                "Accept": "application/json",
            },
        )
        ur.raise_for_status()
        profile = ur.json()
        sub = str(profile.get("login") or profile.get("id") or "github-user")
        return _mock_tokens(subject=sub, role=_default_oauth_role())
