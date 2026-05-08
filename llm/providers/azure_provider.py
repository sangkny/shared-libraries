# shared-libraries/llm/providers/azure_provider.py
"""
Azure OpenAI Provider — 기업 고객용
운영 환경: {resource}.openai.azure.com
"""
import os, time
from openai import AsyncAzureOpenAI
from ..base import BaseProvider, LLMProvider, ModelRole, LLMRequest, LLMResponse, EmbedResponse


class AzureProvider(BaseProvider):
    """
    Azure OpenAI Provider — 기업 고객 전용
    - 데이터가 Azure 리전 내에 머무름 (의료/금융 규제 준수)
    - FAST:  gpt-4o-mini 배포명
    - HEAVY: gpt-4o 배포명
    - EMBED: text-embedding-3-small 배포명
    설정: Azure Portal에서 배포한 모델의 '배포 이름' 사용
    """

    def __init__(self):
        endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        api_key  = os.getenv("AZURE_OPENAI_API_KEY")
        if not endpoint or not api_key:
            raise ValueError("AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY 환경변수 필요")
        self._client = AsyncAzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01"),
        )

    @property
    def provider_name(self) -> LLMProvider:
        return LLMProvider.AZURE

    @property
    def model_map(self) -> dict[ModelRole, str]:
        # Azure는 모델 ID가 아닌 '배포 이름' 사용
        return {
            ModelRole.FAST:   os.getenv("AZURE_FAST_DEPLOYMENT",   "gpt-4o-mini"),
            ModelRole.HEAVY:  os.getenv("AZURE_HEAVY_DEPLOYMENT",  "gpt-4o"),
            ModelRole.VISION: os.getenv("AZURE_VISION_DEPLOYMENT", "gpt-4o"),
            ModelRole.EMBED:  os.getenv("AZURE_EMBED_DEPLOYMENT",  "text-embedding-3-small"),
            ModelRole.BACKUP: os.getenv("AZURE_BACKUP_DEPLOYMENT", "gpt-4o-mini"),
        }

    async def chat(self, request: LLMRequest) -> LLMResponse:
        deployment = self.get_model(request.role)
        messages   = [{"role": m.role, "content": m.content} for m in request.messages]

        t0  = time.monotonic()
        res = await self._client.chat.completions.create(
            model=deployment,
            messages=messages,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
        )
        latency = (time.monotonic() - t0) * 1000

        return LLMResponse(
            content=res.choices[0].message.content,
            model_used=deployment,
            provider=self.provider_name,
            role=request.role,
            input_tokens=res.usage.prompt_tokens,
            output_tokens=res.usage.completion_tokens,
            latency_ms=latency,
        )

    async def embed(self, text: str) -> EmbedResponse:
        deployment = self.get_model(ModelRole.EMBED)
        res = await self._client.embeddings.create(model=deployment, input=text)
        emb = res.data[0].embedding
        return EmbedResponse(
            embedding=emb,
            model_used=deployment,
            provider=self.provider_name,
            dimensions=len(emb),
        )

    def health_check(self) -> dict:
        try:
            endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
            return {"provider": "azure", "status": "ok", "endpoint": endpoint}
        except Exception as e:
            return {"provider": "azure", "status": "error", "error": str(e)}
