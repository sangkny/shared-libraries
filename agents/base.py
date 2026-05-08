# shared-libraries/agents/base.py
"""
BaseAgent — 모든 Agent의 추상 기반 클래스
PlannerAgent / GeneratorAgent / ReviewerAgent / FixerAgent 공통 인터페이스
"""
import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from llm.client import LLMClient
from llm.base import ModelRole
from ontology.base import OntologyDomain, ValidationResult


# ── Agent 타입 ────────────────────────────────────────────
class AgentType(Enum):
    PLANNER    = "planner"
    GENERATOR  = "generator"
    REVIEWER   = "reviewer"
    FIXER      = "fixer"
    ORCHESTRATOR = "orchestrator"


# ── Agent 상태 ────────────────────────────────────────────
class AgentStatus(Enum):
    IDLE       = "idle"
    RUNNING    = "running"
    COMPLETED  = "completed"
    FAILED     = "failed"


# ── Agent 실행 결과 ───────────────────────────────────────
@dataclass
class AgentResult:
    """단일 Agent 실행 결과"""
    agent_type:   AgentType
    task_id:      str
    status:       AgentStatus
    output:       Any                        # 실제 결과물
    model_used:   str        = ""
    latency_ms:   float      = 0.0
    iteration:    int        = 0
    metadata:     dict       = field(default_factory=dict)
    created_at:   str        = field(
        default_factory=lambda: datetime.now().isoformat()
    )
    error:        str        = ""            # 실패 시 오류 메시지

    @property
    def success(self) -> bool:
        return self.status == AgentStatus.COMPLETED

    def to_dict(self) -> dict:
        return {
            "agent_type": self.agent_type.value,
            "task_id":    self.task_id,
            "status":     self.status.value,
            "output":     str(self.output)[:500],  # 로그용 축약
            "model_used": self.model_used,
            "latency_ms": round(self.latency_ms, 2),
            "iteration":  self.iteration,
            "created_at": self.created_at,
            "error":      self.error,
        }


# ── Lore (의사결정 추적) ──────────────────────────────────
@dataclass
class LoreEntry:
    """
    autopus-ADK Lore decision tracking
    모든 Agent의 의사결정을 추적 — 의료 감사추적(Audit Trail) 역할
    """
    task_id:    str
    agent:      AgentType
    action:     str          # "plan" | "generate" | "review" | "fix"
    input_hash: str          # 입력 데이터 해시 (개인정보 보호)
    decision:   str          # 결정 내용 요약 (200자)
    model_used: str
    passed:     bool
    iteration:  int
    timestamp:  str = field(default_factory=lambda: datetime.now().isoformat())

    @staticmethod
    def hash_input(data: Any) -> str:
        import hashlib
        return hashlib.sha256(str(data).encode()).hexdigest()[:16]


# ── BaseAgent ─────────────────────────────────────────────
class BaseAgent(ABC):
    """
    모든 Agent의 추상 기반 클래스

    각 Agent는 run() 메서드를 구현하고
    LLMClient를 통해 LLM을 호출합니다.
    """

    def __init__(
        self,
        domain:    OntologyDomain = OntologyDomain.GENERAL,
        llm:       LLMClient | None = None,
        task_id:   str | None = None,
    ):
        self.domain  = domain
        self.llm     = llm or LLMClient()
        self.task_id = task_id or str(uuid.uuid4())[:8]
        self.log     = logging.getLogger(f"agent.{self.agent_type.value}")
        self._lore:  list[LoreEntry] = []

    @property
    @abstractmethod
    def agent_type(self) -> AgentType:
        ...

    @property
    @abstractmethod
    def model_role(self) -> ModelRole:
        """이 Agent가 사용하는 기본 ModelRole"""
        ...

    @abstractmethod
    async def run(self, task: str, context: dict | None = None) -> AgentResult:
        """
        Agent 실행
        Args:
            task:    수행할 작업 설명
            context: 추가 컨텍스트 (이전 Agent 결과 등)
        Returns:
            AgentResult
        """
        ...

    # ── 공통 헬퍼 ──────────────────────────────────────────

    def _ok(self, output: Any, model: str = "",
            latency: float = 0.0, iteration: int = 0) -> AgentResult:
        """성공 결과 생성"""
        return AgentResult(
            agent_type=self.agent_type,
            task_id=self.task_id,
            status=AgentStatus.COMPLETED,
            output=output,
            model_used=model,
            latency_ms=latency,
            iteration=iteration,
        )

    def _fail(self, error: str, iteration: int = 0) -> AgentResult:
        """실패 결과 생성"""
        self.log.error(f"[{self.task_id}] 실패: {error}")
        return AgentResult(
            agent_type=self.agent_type,
            task_id=self.task_id,
            status=AgentStatus.FAILED,
            output=None,
            error=error,
            iteration=iteration,
        )

    def _record_lore(self, action: str, input_data: Any,
                     decision: str, model: str,
                     passed: bool, iteration: int):
        """Lore 의사결정 기록"""
        entry = LoreEntry(
            task_id=self.task_id,
            agent=self.agent_type,
            action=action,
            input_hash=LoreEntry.hash_input(input_data),
            decision=decision[:200],
            model_used=model,
            passed=passed,
            iteration=iteration,
        )
        self._lore.append(entry)
        self.log.debug(
            f"[LORE] {action} | passed={passed} | "
            f"model={model} | iter={iteration}"
        )

    @property
    def lore(self) -> list[LoreEntry]:
        return self._lore.copy()

    def _build_system_prompt(self, extra: str = "") -> str:
        """도메인별 시스템 프롬프트 생성"""
        domain_context = {
            OntologyDomain.MEDICAL:  (
                "당신은 의료 AI 전문가입니다. "
                "MEDI-IOT EyeCare 플랫폼을 위한 안과 의료 데이터를 처리합니다. "
                "ICD-10 표준, 의료 데이터 보안, 환자 개인정보 보호를 준수합니다."
            ),
            OntologyDomain.SOFTWARE: (
                "당신은 시니어 소프트웨어 엔지니어입니다. "
                "AutoNoGaDa 플랫폼을 위한 고품질 Python 코드를 생성합니다. "
                "PEP8, 타입 힌트, 단일 책임 원칙을 준수합니다."
            ),
            OntologyDomain.BUSINESS: (
                "당신은 비즈니스 프로세스 전문가입니다. "
                "CoOps 플랫폼을 위한 업무 자동화 프로세스를 설계합니다. "
                "효율성, 규정 준수, 감사 추적을 중시합니다."
            ),
            OntologyDomain.GENERAL: "당신은 유능한 AI 어시스턴트입니다.",
        }
        base = domain_context.get(self.domain, "")
        return f"{base} {extra}".strip()
