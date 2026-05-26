# shared-libraries/llm/tests/test_providers.py
"""
LLM Provider 통합 테스트
실행: pytest tests/test_providers.py -v
"""
import pytest, asyncio, os
from unittest.mock import AsyncMock, patch, MagicMock
from llm.client import LLMClient, quick_chat
from llm.base import ModelRole, LLMProvider, LLMResponse, EmbedResponse, Message, LLMRequest


# ── Fixtures ──────────────────────────────────────────────

def make_mock_response(content="테스트 응답", model="test-model", provider=LLMProvider.LOCAL):
    return LLMResponse(
        content=content,
        model_used=model,
        provider=provider,
        role=ModelRole.FAST,
        input_tokens=10,
        output_tokens=20,
        latency_ms=100.0,
    )

def make_mock_embed(dims=768):
    return EmbedResponse(
        embedding=[0.1] * dims,
        model_used="test-embed",
        provider=LLMProvider.LOCAL,
        dimensions=dims,
    )


# ── Local Provider 테스트 ──────────────────────────────────

class TestLocalProvider:
    @pytest.mark.asyncio
    async def test_chat_fast_model(self):
        """FAST 역할로 채팅 요청"""
        with patch("openai.AsyncOpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_openai.return_value = mock_client
            mock_client.chat.completions.create = AsyncMock(
                return_value=MagicMock(
                    choices=[MagicMock(message=MagicMock(content="응답"))],
                    usage=MagicMock(prompt_tokens=5, completion_tokens=10),
                )
            )
            from llm.providers.local import LocalProvider
            provider = LocalProvider()
            provider._client = mock_client

            req = LLMRequest(
                messages=[Message(role="user", content="안녕하세요")],
                role=ModelRole.FAST,
            )
            res = await provider.chat(req)
            assert res.content == "응답"
            assert res.provider == LLMProvider.LOCAL

    @pytest.mark.asyncio
    async def test_embed(self):
        """임베딩 요청"""
        with patch("openai.AsyncOpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_openai.return_value = mock_client
            mock_client.embeddings.create = AsyncMock(
                return_value=MagicMock(
                    data=[MagicMock(embedding=[0.1] * 768)]
                )
            )
            from llm.providers.local import LocalProvider
            provider = LocalProvider()
            provider._client = mock_client

            res = await provider.embed("테스트 텍스트")
            assert len(res.embedding) == 768
            assert res.dimensions == 768


# ── LLMClient 테스트 ──────────────────────────────────────

class TestLLMClient:
    @pytest.mark.asyncio
    async def test_provider_selection_local(self):
        """LOCAL Provider 선택"""
        os.environ["LLM_PROVIDER"] = "local"
        with patch("llm.providers.local.LocalProvider.chat",
                   new_callable=AsyncMock, return_value=make_mock_response()):
            client = LLMClient(provider="local")
            assert client.current_provider == LLMProvider.LOCAL

    @pytest.mark.asyncio
    async def test_fallback_on_failure(self):
        """메인 Provider 실패 시 LOCAL fallback"""
        call_count = {"n": 0}

        async def failing_chat(req):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise ConnectionError("API 연결 실패")
            return make_mock_response(content="fallback 응답", provider=LLMProvider.LOCAL)

        with patch("llm.providers.openai_provider.OpenAIProvider") as MockOAI, \
             patch("llm.providers.local.LocalProvider") as MockLocal:

            mock_openai = MagicMock()
            mock_openai.chat = AsyncMock(side_effect=ConnectionError("API 실패"))
            mock_openai.provider_name = LLMProvider.OPENAI
            mock_openai.model_map = {ModelRole.FAST: "gpt-4o-mini"}
            mock_openai.get_model = lambda r: "gpt-4o-mini"
            MockOAI.return_value = mock_openai

            mock_local = MagicMock()
            mock_local.chat = AsyncMock(return_value=make_mock_response(content="fallback 응답"))
            mock_local.provider_name = LLMProvider.LOCAL
            mock_local.health_check = MagicMock(return_value={"status": "ok"})
            MockLocal.return_value = mock_local

            os.environ["LLM_FALLBACK_ENABLED"] = "true"
            client = LLMClient.__new__(LLMClient)
            client._provider = mock_openai
            client._embed_provider = mock_local
            client._fallback = mock_local
            client._fallback_enabled = True

            res = await client.chat("테스트")
            assert res.content == "fallback 응답"
            assert res.metadata.get("fallback_reason")

    @pytest.mark.asyncio
    async def test_embed_fallback_for_anthropic(self):
        """Anthropic Provider → 임베딩 LOCAL fallback"""
        from llm.providers.anthropic_provider import AnthropicProvider
        os.environ["ANTHROPIC_API_KEY"] = "test-key"

        with patch("anthropic.AsyncAnthropic"):
            provider = AnthropicProvider()
            with pytest.raises(NotImplementedError) as exc_info:
                await provider.embed("텍스트")
            assert "임베딩을 지원하지 않습니다" in str(exc_info.value)

    def test_model_role_fallback(self):
        """VISION role 미지원 시 HEAVY로 fallback"""
        from llm.providers.local import LocalProvider
        provider = LocalProvider()
        # VISION이 설정되어 있으면 그것을 반환
        model = provider.get_model(ModelRole.VISION)
        assert model != ""  # 비어있지 않음


# ── Provider 비교 테스트 ───────────────────────────────────

class TestProviderModels:
    """각 Provider의 ModelRole 매핑 확인"""

    def test_local_model_map(self):
        from llm.providers.local import LocalProvider
        p = LocalProvider()
        assert ModelRole.FAST   in p.model_map
        assert ModelRole.HEAVY  in p.model_map
        assert ModelRole.EMBED  in p.model_map
        assert ModelRole.VISION in p.model_map

    def test_google_model_map(self):
        os.environ["GOOGLE_API_KEY"] = "test-key"
        with patch("llm.providers.google_provider.genai.Client"):
            from llm.providers.google_provider import GoogleProvider
            p = GoogleProvider()
            assert "gemini" in p.model_map[ModelRole.FAST]
            assert "gemini" in p.model_map[ModelRole.HEAVY]
            assert "embedding" in p.model_map[ModelRole.EMBED]

    def test_openai_model_map(self):
        os.environ["OPENAI_API_KEY"] = "test-key"
        from llm.providers.openai_provider import OpenAIProvider
        p = OpenAIProvider()
        assert "gpt" in p.model_map[ModelRole.FAST]
        assert "gpt" in p.model_map[ModelRole.HEAVY]
        assert "embedding" in p.model_map[ModelRole.EMBED]

    def test_anthropic_model_map(self):
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        with patch("anthropic.AsyncAnthropic"):
            from llm.providers.anthropic_provider import AnthropicProvider
            p = AnthropicProvider()
            assert "claude" in p.model_map[ModelRole.FAST]
            assert "claude" in p.model_map[ModelRole.HEAVY]
