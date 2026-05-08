# shared-libraries/llm/client.py
"""
LLMClient — Provider 추상화 통합 클라이언트
환경변수 LLM_PROVIDER 하나로 Provider 전환
자동 Fallback: 실패 시 LOCAL로 재시도
"""
import os, logging, asyncio
from typing import Optional
from .base import (
    BaseProvider, LLMProvider, ModelRole,
    LLMRequest, LLMResponse, EmbedResponse, Message
)

log = logging.getLogger("llm.client")


# ── Provider 팩토리 ──────────────────────────────────────
def _build_provider(name: str) -> BaseProvider:
    """환경변수 값으로 Provider 인스턴스 생성"""
    p = LLMProvider(name.lower())

    if p == LLMProvider.LOCAL:
        from .providers.local import LocalProvider
        return LocalProvider()
    elif p == LLMProvider.OPENAI:
        from .providers.openai_provider import OpenAIProvider
        return OpenAIProvider()
    elif p == LLMProvider.ANTHROPIC:
        from .providers.anthropic_provider import AnthropicProvider
        return AnthropicProvider()
    elif p == LLMProvider.GOOGLE:
        from .providers.google_provider import GoogleProvider
        return GoogleProvider()
    elif p == LLMProvider.AZURE:
        from .providers.azure_provider import AzureProvider
        return AzureProvider()
    else:
        raise ValueError(f"지원하지 않는 Provider: {name}")


# ── 메인 클라이언트 ──────────────────────────────────────
class LLMClient:
    """
    Provider 독립적인 통합 LLM 클라이언트

    사용법:
        # 환경변수로 Provider 선택
        # LLM_PROVIDER=local    → LM Studio
        # LLM_PROVIDER=openai   → GPT-4o
        # LLM_PROVIDER=anthropic → Claude
        # LLM_PROVIDER=google   → Gemini
        # LLM_PROVIDER=azure    → Azure OpenAI

        client = LLMClient()

        # 간단한 채팅
        response = await client.chat("안녕하세요")

        # 역할 지정
        response = await client.chat("복잡한 분석", role=ModelRole.HEAVY)

        # 시스템 프롬프트 포함
        response = await client.chat(
            "환자 데이터 분석",
            role=ModelRole.VISION,
            system="당신은 안과 전문의 AI 어시스턴트입니다."
        )

        # 임베딩
        vector = await client.embed("검색할 텍스트")

        # 임베딩은 별도 Provider 사용 가능 (Anthropic 사용 시)
        # LLM_EMBED_PROVIDER=local 설정으로 임베딩만 LOCAL 사용
    """

    def __init__(
        self,
        provider: Optional[str] = None,
        embed_provider: Optional[str] = None,
    ):
        # 메인 Provider
        provider_name = provider or os.getenv("LLM_PROVIDER", "local")
        self._provider: BaseProvider = _build_provider(provider_name)
        log.info(f"LLMClient 초기화 — provider={provider_name}")

        # 임베딩 전용 Provider (Anthropic처럼 임베딩 미지원 Provider 대응)
        embed_name = embed_provider or os.getenv("LLM_EMBED_PROVIDER", provider_name)
        if embed_name != provider_name:
            self._embed_provider: BaseProvider = _build_provider(embed_name)
            log.info(f"임베딩 전용 provider={embed_name}")
        else:
            self._embed_provider = self._provider

        # Fallback Provider (실패 시 LOCAL로 재시도)
        self._fallback_enabled = os.getenv("LLM_FALLBACK_ENABLED", "true").lower() == "true"
        if self._fallback_enabled and provider_name != "local":
            try:
                self._fallback: Optional[BaseProvider] = _build_provider("local")
                log.info("Fallback provider=local 준비 완료")
            except Exception:
                self._fallback = None
        else:
            self._fallback = None

    # ── 공개 API ────────────────────────────────────────

    async def chat(
        self,
        prompt: str,
        role: ModelRole = ModelRole.FAST,
        system: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        **kwargs,
    ) -> LLMResponse:
        """
        채팅 완성 요청 (Provider 독립적)
        실패 시 Fallback Provider로 자동 재시도
        """
        messages = []
        if system:
            messages.append(Message(role="system", content=system))
        messages.append(Message(role="user", content=prompt))

        request = LLMRequest(
            messages=messages,
            role=role,
            max_tokens=max_tokens,
            temperature=temperature,
            metadata=kwargs,
        )
        return await self._chat_with_fallback(request)

    async def chat_messages(
        self,
        messages: list[dict],
        role: ModelRole = ModelRole.FAST,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> LLMResponse:
        """
        멀티턴 대화용 — messages 리스트 직접 전달
        messages: [{"role": "user"|"assistant"|"system", "content": "..."}]
        """
        msg_objs = [Message(role=m["role"], content=m["content"]) for m in messages]
        request  = LLMRequest(
            messages=msg_objs,
            role=role,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return await self._chat_with_fallback(request)

    async def embed(self, text: str) -> EmbedResponse:
        """
        텍스트 임베딩 요청
        LLM_EMBED_PROVIDER 환경변수로 임베딩 전용 Provider 선택 가능
        """
        try:
            return await self._embed_provider.embed(text)
        except NotImplementedError:
            # 임베딩 미지원 Provider → LOCAL fallback
            log.warning(f"{self._embed_provider.provider_name.value}은 임베딩 미지원 → LOCAL fallback")
            from .providers.local import LocalProvider
            return await LocalProvider().embed(text)
        except Exception as e:
            log.error(f"임베딩 오류: {e}")
            raise

    def health_check_all(self) -> dict:
        """전체 Provider 상태 확인"""
        results = {
            "main":     self._provider.health_check(),
            "embed":    self._embed_provider.health_check(),
        }
        if self._fallback:
            results["fallback"] = self._fallback.health_check()
        return results

    @property
    def current_provider(self) -> LLMProvider:
        return self._provider.provider_name

    # ── 내부 메서드 ────────────────────────────────────

    async def _chat_with_fallback(self, request: LLMRequest) -> LLMResponse:
        """Fallback 포함 채팅 실행"""
        try:
            return await self._provider.chat(request)
        except Exception as e:
            if self._fallback:
                log.warning(
                    f"[{self._provider.provider_name.value}] 오류 → LOCAL fallback 시도: {e}"
                )
                try:
                    res = await self._fallback.chat(request)
                    res.metadata["fallback_reason"] = str(e)
                    return res
                except Exception as fe:
                    log.error(f"Fallback도 실패: {fe}")
                    raise fe
            raise


# ── 편의 함수 (동기 환경용) ──────────────────────────────
def quick_chat(prompt: str, role: ModelRole = ModelRole.FAST, system: str = None) -> str:
    """
    동기 환경에서 간단하게 LLM 호출
    Jupyter, 스크립트 등에서 사용
    """
    client = LLMClient()
    return asyncio.run(client.chat(prompt, role=role, system=system)).content
