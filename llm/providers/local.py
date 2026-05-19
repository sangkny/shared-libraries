# shared-libraries/llm/providers/local.py
"""
Local Provider — LM Studio (OpenAI 호환 API)
개발 환경: http://host.docker.internal:8000/v1
"""
import os, time
from openai import AsyncOpenAI
from ..base import BaseProvider, LLMProvider, ModelRole, LLMRequest, LLMResponse, EmbedResponse, Message


class LocalProvider(BaseProvider):
    """
    LM Studio Local LLM Provider
    - 포트: 8000 (기본값, 환경변수로 변경 가능)
    - 현재 모델: gemma-4-e4b, gemma-4-26b-a4b, mistral-7b, nomic-embed
    - OpenAI 호환 API 사용
    """

    def __init__(self):
        self._client = AsyncOpenAI(
            base_url=os.getenv("LOCAL_BASE_URL", "http://host.docker.internal:8000/v1"),
            api_key=os.getenv("LOCAL_API_KEY", "lm-studio"),  # LM Studio는 아무 값이나 OK
        )

    @property
    def provider_name(self) -> LLMProvider:
        return LLMProvider.LOCAL

    @property
    def model_map(self) -> dict[ModelRole, str]:
        return {
            ModelRole.FAST:   os.getenv("LOCAL_FAST_MODEL",   "google/gemma-4-e4b"),
            ModelRole.HEAVY:  os.getenv("LOCAL_HEAVY_MODEL",  "google/gemma-4-26b-a4b"),
            ModelRole.VISION: os.getenv("LOCAL_VISION_MODEL", "google/gemma-4-26b-a4b"),  # A4B = 멀티모달
            ModelRole.EMBED:  os.getenv("LOCAL_EMBED_MODEL",  "text-embedding-nomic-embed-text-v1.5"),
            ModelRole.BACKUP: os.getenv("LOCAL_BACKUP_MODEL", "mistralai/mistral-7b-instruct-v0.3"),
        }

    async def chat(self, request: LLMRequest) -> LLMResponse:
        # MEDI D R3 — 요청별 model_id override (multi-modal 라우팅)
        model_id = request.metadata.get("model_id") or self.get_model(request.role)
        messages = [{"role": m.role, "content": m.content} for m in request.messages]

        t0 = time.monotonic()
        res = await self._client.chat.completions.create(
            model=model_id,
            messages=messages,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            stream=False,
        )
        latency = (time.monotonic() - t0) * 1000

        return LLMResponse(
            content=res.choices[0].message.content,
            model_used=model_id,
            provider=self.provider_name,
            role=request.role,
            input_tokens=getattr(res.usage, "prompt_tokens", 0),
            output_tokens=getattr(res.usage, "completion_tokens", 0),
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
        import httpx
        try:
            base = os.getenv("LOCAL_BASE_URL", "http://host.docker.internal:8000/v1")
            # 동기 클라이언트 사용 — event loop 충돌 방지
            with httpx.Client(timeout=3) as client:
                r = client.get(f"{base}/models")
            models = [m["id"] for m in r.json().get("data", [])]
            return {"provider": "local", "status": "ok", "models": models}
        except Exception as e:
            return {"provider": "local", "status": "error", "error": str(e)}
