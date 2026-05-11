"""OAuth2 교환·Mock — Phase 2 Week 11."""
from __future__ import annotations

import os

import pytest

from auth.jwt_handler import verify_refresh_payload
from auth.oauth2 import exchange_github_code, exchange_google_code


@pytest.mark.asyncio
async def test_google_mock_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    out = await exchange_google_code("mock_google_xyz", None)
    assert out["token_type"] == "bearer"
    assert "access_token" in out and "refresh_token" in out
    p = verify_refresh_payload(out["refresh_token"])
    assert p.get("typ") == "refresh"


@pytest.mark.asyncio
async def test_github_mock_code_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_CLIENT_ID", "real")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "real")
    out = await exchange_github_code("mock_skip_http", None)
    assert out["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_github_mock_no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_CLIENT_ID", raising=False)
    monkeypatch.delenv("GITHUB_CLIENT_SECRET", raising=False)
    out = await exchange_github_code("any", None)
    assert "access_token" in out
