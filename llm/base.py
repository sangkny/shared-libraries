# shared-libraries/llm/base.py
"""
LLM Provider 추상 기반 클래스
모든 Provider는 이 인터페이스를 구현해야 합니다.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator


class ModelRole(Enum):
    """Provider에 독립적인 모델 역할 정의"""
    FAST   = "fast"    # 빠른 응답 — Agent 루프, 반복 작업
    HEAVY  = "heavy"   # 고품질 추론 — 최종 검토, 복잡한 분석
    EMBED  = "embed"   # 벡터 임베딩 — RAG, 유사도 검색
    VISION = "vision"  # 멀티모달 — 이미지+텍스트 (MEDI-IOT 안과)
    BACKUP = "backup"  # Fallback — 기본 모델 실패 시


class LLMProvider(Enum):
    """지원 Provider 목록"""
    LOCAL     = "local"      # LM Studio (개발/로컬)
    OPENAI    = "openai"     # OpenAI GPT 시리즈
    ANTHROPIC = "anthropic"  # Anthropic Claude 시리즈
    GOOGLE    = "google"     # Google Gemini 시리즈
    AZURE     = "azure"      # Azure OpenAI (기업 고객용)


@dataclass
class Message:
    """단일 대화 메시지"""
    role: str     # "system" | "user" | "assistant"
    content: str


@dataclass
class LLMRequest:
    """Provider 독립적인 LLM 요청 구조"""
    messages:     list[Message]
    role:         ModelRole = ModelRole.FAST
    max_tokens:   int       = 1024
    temperature:  float     = 0.7
    stream:       bool      = False
    metadata:     dict      = field(default_factory=dict)  # 도메인 정보 등


@dataclass
class LLMResponse:
    """Provider 독립적인 LLM 응답 구조"""
    content:       str
    model_used:    str            # 실제 사용된 모델 ID
    provider:      LLMProvider
    role:          ModelRole
    input_tokens:  int   = 0
    output_tokens: int   = 0
    latency_ms:    float = 0.0
    metadata:      dict  = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class EmbedResponse:
    """임베딩 응답 구조"""
    embedding:  list[float]
    model_used: str
    provider:   LLMProvider
    dimensions: int = 0


class BaseProvider(ABC):
    """
    모든 LLM Provider의 추상 기반 클래스
    새로운 Provider 추가 시 이 클래스를 상속하여 구현
    """

    @property
    @abstractmethod
    def provider_name(self) -> LLMProvider:
        """Provider 식별자"""
        ...

    @property
    @abstractmethod
    def model_map(self) -> dict[ModelRole, str]:
        """ModelRole → 실제 모델 ID 매핑"""
        ...

    @abstractmethod
    async def chat(self, request: LLMRequest) -> LLMResponse:
        """
        채팅 완성 요청
        Args:
            request: LLMRequest 객체
        Returns:
            LLMResponse 객체
        """
        ...

    @abstractmethod
    async def embed(self, text: str) -> EmbedResponse:
        """
        텍스트 임베딩 요청
        Args:
            text: 임베딩할 텍스트
        Returns:
            EmbedResponse 객체
        """
        ...

    async def stream_chat(self, request: LLMRequest) -> AsyncIterator[str]:
        """
        스트리밍 채팅 (선택적 구현)
        기본 구현: 일반 chat() 결과를 단일 청크로 반환
        """
        response = await self.chat(request)
        yield response.content

    def get_model(self, role: ModelRole) -> str:
        """ModelRole에 해당하는 실제 모델 ID 반환"""
        model = self.model_map.get(role)
        if not model:
            # VISION이 없으면 HEAVY로 fallback
            if role == ModelRole.VISION:
                model = self.model_map.get(ModelRole.HEAVY, "")
            # BACKUP이 없으면 FAST로 fallback
            elif role == ModelRole.BACKUP:
                model = self.model_map.get(ModelRole.FAST, "")
        if not model:
            raise ValueError(f"[{self.provider_name.value}] ModelRole.{role.name} 에 해당하는 모델이 없습니다.")
        return model

    def health_check(self) -> dict:
        """Provider 상태 확인 (서브클래스에서 오버라이드 권장)"""
        return {"provider": self.provider_name.value, "status": "unknown"}
