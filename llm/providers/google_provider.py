# shared-libraries/llm/providers/google_provider.py
"""
Google Gemini Provider — google-genai (신규 패키지)
"""
import os, time
from google import genai
from google.genai import types
from ..base import BaseProvider, LLMProvider, ModelRole, LLMRequest, LLMResponse, EmbedResponse
 
 
class GoogleProvider(BaseProvider):
    def __init__(self):
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_API_KEY 환경변수가 설정되지 않았습니다.")
        self._client = genai.Client(api_key=api_key)
 
    @property
    def provider_name(self) -> LLMProvider:
        return LLMProvider.GOOGLE
 
    @property
    def model_map(self) -> dict[ModelRole, str]:
        return {
            ModelRole.FAST:   os.getenv("GOOGLE_FAST_MODEL",   "gemini-2.0-flash"),
            ModelRole.HEAVY:  os.getenv("GOOGLE_HEAVY_MODEL",  "gemini-1.5-pro"),
            ModelRole.VISION: os.getenv("GOOGLE_VISION_MODEL", "gemini-2.0-flash"),
            ModelRole.EMBED:  os.getenv("GOOGLE_EMBED_MODEL",  "text-embedding-004"),
            ModelRole.BACKUP: os.getenv("GOOGLE_BACKUP_MODEL", "gemini-1.5-flash"),
        }
 
    async def chat(self, request: LLMRequest) -> LLMResponse:
        model_id = self.get_model(request.role)
        system_content = ""
        contents = []
        for m in request.messages:
            if m.role == "system":
                system_content = m.content
            elif m.role == "user":
                contents.append(types.Content(role="user", parts=[types.Part(text=m.content)]))
            elif m.role == "assistant":
                contents.append(types.Content(role="model", parts=[types.Part(text=m.content)]))
 
        config = types.GenerateContentConfig(
            max_output_tokens=request.max_tokens,
            temperature=request.temperature,
            system_instruction=system_content if system_content else None,
        )
        t0 = time.monotonic()
        res = await self._client.aio.models.generate_content(
            model=model_id, contents=contents, config=config,
        )
        latency = (time.monotonic() - t0) * 1000
        usage = getattr(res, "usage_metadata", None)
        return LLMResponse(
            content=res.text or "",
            model_used=model_id,
            provider=self.provider_name,
            role=request.role,
            input_tokens=getattr(usage, "prompt_token_count", 0),
            output_tokens=getattr(usage, "candidates_token_count", 0),
            latency_ms=latency,
        )
 
    async def embed(self, text: str) -> EmbedResponse:
        model_id = self.get_model(ModelRole.EMBED)
        res = await self._client.aio.models.embed_content(model=model_id, contents=text)
        emb = res.embeddings[0].values
        return EmbedResponse(embedding=list(emb), model_used=model_id,
                             provider=self.provider_name, dimensions=len(emb))
 
    def health_check(self) -> dict:
        try:
            models = list(self._client.models.list())
            return {"provider": "google", "status": "ok", "model_count": len(models)}
        except Exception as e:
            return {"provider": "google", "status": "error", "error": str(e)}
 