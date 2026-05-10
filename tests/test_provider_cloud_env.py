"""
Cloud Provider 전환 Mock (Week 7 Day 2 — API 키 없을 때).

`pytest -k provider` 로만 수집되도록 클래스/함수명에 provider 포함.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from llm.client import _build_provider
from llm.base import LLMProvider


class TestProviderFactorySelection:
    def test_provider_openai_factory(self) -> None:
        with patch.dict(
            os.environ,
            {"LLM_PROVIDER": "openai", "OPENAI_API_KEY": "dummy"},
            clear=False,
        ):
            p = _build_provider("openai")
            assert p.provider_name == LLMProvider.OPENAI

    def test_provider_anthropic_factory(self) -> None:
        with patch.dict(
            os.environ,
            {"LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "dummy"},
            clear=False,
        ):
            p = _build_provider("anthropic")
            assert p.provider_name == LLMProvider.ANTHROPIC

    def test_provider_google_factory(self) -> None:
        with patch.dict(
            os.environ,
            {"LLM_PROVIDER": "google", "GOOGLE_API_KEY": "dummy"},
            clear=False,
        ):
            with patch(
                "llm.providers.google_provider.genai.Client",
                MagicMock(return_value=MagicMock()),
            ):
                p = _build_provider("google")
            assert p.provider_name == LLMProvider.GOOGLE
