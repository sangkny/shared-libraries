# shared-libraries/llm/providers/openai_provider.py
"""
OpenAI Provider — GPT-4o, GPT-4o-mini, text-embedding-3
운영 환경: api.openai.com
"""
import os, time
from openai import AsyncOpenAI
from ..base import BaseProvider, LLMProvider, ModelRole, LLMRequest, LLMResponse, EmbedResponse


class OpenAIProvider(BaseProvider):
    """
    OpenAI GPT Provider
    - FAST:   gpt-4o-mini  (빠르고 저렴)
    - HEAVY:  gpt-4o       (고품질)
    - VISION: gpt-4o       (멀티모달 지원)
    - EMBED:  text-embedding-3-small
    - BACKUP: gpt-3.5-turbo
    """

    def __init__(self):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY 환경변수가 설정되지 않았습니다.")
        self._client = AsyncOpenAI(api_key=api_key)

    @property
    def provider_name(self) -> LLMProvider:
        return LLMProvider.OPENAI

    @property
    def model_map(self) -> dict[ModelRole, str]:
        return {
            ModelRole.FAST:   os.getenv("OPENAI_FAST_MODEL",   "gpt-4o-mini"),
            ModelRole.HEAVY:  os.getenv("OPENAI_HEAVY_MODEL",  "gpt-4o"),
            ModelRole.VISION: os.getenv("OPENAI_VISION_MODEL", "gpt-4o"),
            ModelRole.EMBED:  os.getenv("OPENAI_EMBED_MODEL",  "text-embedding-3-small"),
            ModelRole.BACKUP: os.getenv("OPENAI_BACKUP_MODEL", "gpt-3.5-turbo"),
        }

    async def chat(self, request: LLMRequest) -> LLMResponse:
        model_id = self.get_model(request.role)
        messages = [{"role": m.role, "content": m.content} for m in request.messages]

        t0 = time.monotonic()
        res = await self._client.chat.completions.create(
            model=model_id,
            messages=messages,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
        )
        latency = (time.monotonic() - t0) * 1000

        return LLMResponse(
            content=res.choices[0].message.content,
            model_used=model_id,
            provider=self.provider_name,
            role=request.role,
            input_tokens=res.usage.prompt_tokens,
            output_tokens=res.usage.completion_tokens,
            latency_ms=latency,
        )

    async def embed(self, text: str) -> EmbedResponse:
        model_id = self.get_model(ModelRole.EMBED)
        res = await self._client.embeddings.create(model=model_id, input=text)
        emb = res.data[0].embedding
        return EmbedResponse(
            embedding=emb,
            model_used=model_id,
            provider=self.provider_name,
            dimensions=len(emb),
        )

    def health_check(self) -> dict:
        try:
            import httpx
            key = os.getenv("OPENAI_API_KEY", "")
            r = httpx.get("https://api.openai.com/v1/models",
                          headers={"Authorization": f"Bearer {key}"}, timeout=5)
            return {"provider": "openai", "status": "ok" if r.status_code == 200 else "error"}
        except Exception as e:
            return {"provider": "openai", "status": "error", "error": str(e)}
