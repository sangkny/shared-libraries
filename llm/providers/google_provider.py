# shared-libraries/llm/providers/google_provider.py
"""
Google Gemini Provider — Gemini 2.0 Flash, Gemini 1.5 Pro
운영 환경: generativelanguage.googleapis.com
"""
import os, time
import google.generativeai as genai
from ..base import BaseProvider, LLMProvider, ModelRole, LLMRequest, LLMResponse, EmbedResponse


class GoogleProvider(BaseProvider):
    """
    Google Gemini Provider
    - FAST:   gemini-2.0-flash   (빠르고 저렴, 멀티모달)
    - HEAVY:  gemini-1.5-pro     (고품질 추론, 1M 컨텍스트)
    - VISION: gemini-2.0-flash   (네이티브 멀티모달)
    - EMBED:  text-embedding-004 (자체 임베딩)
    - BACKUP: gemini-1.5-flash
    특징: 1M 토큰 컨텍스트, 네이티브 멀티모달, 무료 티어
    """

    def __init__(self):
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_API_KEY 환경변수가 설정되지 않았습니다.")
        genai.configure(api_key=api_key)

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
        history = []
        last_user_msg = ""

        for m in request.messages:
            if m.role == "system":
                system_content = m.content
            elif m.role == "user":
                last_user_msg = m.content
                history.append({"role": "user", "parts": [m.content]})
            elif m.role == "assistant":
                history.append({"role": "model", "parts": [m.content]})

        model = genai.GenerativeModel(
            model_id,
            system_instruction=system_content if system_content else None,
        )
        gen_config = genai.GenerationConfig(
            max_output_tokens=request.max_tokens,
            temperature=request.temperature,
        )

        t0 = time.monotonic()
        if len(history) > 1:
            chat_session = model.start_chat(history=history[:-1])
            res = await chat_session.send_message_async(last_user_msg, generation_config=gen_config)
        else:
            res = await model.generate_content_async(last_user_msg, generation_config=gen_config)
        latency = (time.monotonic() - t0) * 1000

        content = res.text
        usage   = getattr(res, "usage_metadata", None)
        return LLMResponse(
            content=content,
            model_used=model_id,
            provider=self.provider_name,
            role=request.role,
            input_tokens=getattr(usage, "prompt_token_count", 0),
            output_tokens=getattr(usage, "candidates_token_count", 0),
            latency_ms=latency,
        )

    async def embed(self, text: str) -> EmbedResponse:
        model_id = self.get_model(ModelRole.EMBED)
        result   = genai.embed_content(model=model_id, content=text)
        emb      = result["embedding"]
        return EmbedResponse(
            embedding=emb,
            model_used=model_id,
            provider=self.provider_name,
            dimensions=len(emb),
        )

    def health_check(self) -> dict:
        try:
            models = [m.name for m in genai.list_models()]
            return {"provider": "google", "status": "ok", "model_count": len(models)}
        except Exception as e:
            return {"provider": "google", "status": "error", "error": str(e)}
