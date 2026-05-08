# shared-libraries/llm/providers/anthropic_provider.py
"""
Anthropic Provider — Claude 시리즈
운영 환경: api.anthropic.com
"""
import os, time
import anthropic
from ..base import BaseProvider, LLMProvider, ModelRole, LLMRequest, LLMResponse, EmbedResponse


class AnthropicProvider(BaseProvider):
    """
    Anthropic Claude Provider
    - FAST:   claude-haiku-4-5   (빠르고 저렴)
    - HEAVY:  claude-sonnet-4-6  (고품질 추론)
    - VISION: claude-sonnet-4-6  (멀티모달 지원)
    - EMBED:  없음 → voyage-ai 권장 (별도 통합)
    - BACKUP: claude-haiku-4-5
    참고: Anthropic은 자체 임베딩 API 미제공 → voyage-ai 사용
    """

    def __init__(self):
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY 환경변수가 설정되지 않았습니다.")
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    @property
    def provider_name(self) -> LLMProvider:
        return LLMProvider.ANTHROPIC

    @property
    def model_map(self) -> dict[ModelRole, str]:
        return {
            ModelRole.FAST:   os.getenv("ANTHROPIC_FAST_MODEL",   "claude-haiku-4-5-20251001"),
            ModelRole.HEAVY:  os.getenv("ANTHROPIC_HEAVY_MODEL",  "claude-sonnet-4-6"),
            ModelRole.VISION: os.getenv("ANTHROPIC_VISION_MODEL", "claude-sonnet-4-6"),
            ModelRole.BACKUP: os.getenv("ANTHROPIC_BACKUP_MODEL", "claude-haiku-4-5-20251001"),
            # EMBED는 Anthropic 미지원 — embed() 호출 시 예외 발생
        }

    async def chat(self, request: LLMRequest) -> LLMResponse:
        model_id = self.get_model(request.role)

        # Anthropic API는 system 메시지를 별도 파라미터로 처리
        system_content = ""
        user_messages  = []
        for m in request.messages:
            if m.role == "system":
                system_content = m.content
            else:
                user_messages.append({"role": m.role, "content": m.content})

        t0 = time.monotonic()
        kwargs = dict(
            model=model_id,
            max_tokens=request.max_tokens,
            messages=user_messages,
        )
        if system_content:
            kwargs["system"] = system_content

        res = await self._client.messages.create(**kwargs)
        latency = (time.monotonic() - t0) * 1000

        return LLMResponse(
            content=res.content[0].text,
            model_used=model_id,
            provider=self.provider_name,
            role=request.role,
            input_tokens=res.usage.input_tokens,
            output_tokens=res.usage.output_tokens,
            latency_ms=latency,
        )

    async def embed(self, text: str) -> EmbedResponse:
        # Anthropic은 자체 임베딩 API 미제공
        raise NotImplementedError(
            "Anthropic Provider는 임베딩을 지원하지 않습니다.\n"
            "대안: LLM_EMBED_PROVIDER=local 또는 voyage-ai 사용\n"
            "환경변수 LLM_EMBED_FALLBACK=local 설정 권장"
        )

    def health_check(self) -> dict:
        try:
            import httpx
            key = os.getenv("ANTHROPIC_API_KEY", "")
            r = httpx.get("https://api.anthropic.com/v1/models",
                          headers={"x-api-key": key, "anthropic-version": "2023-06-01"}, timeout=5)
            return {"provider": "anthropic", "status": "ok" if r.status_code == 200 else "error"}
        except Exception as e:
            return {"provider": "anthropic", "status": "error", "error": str(e)}
