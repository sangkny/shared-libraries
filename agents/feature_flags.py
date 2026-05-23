# shared-libraries/agents/feature_flags.py
"""에이전트 결정 모드 피처 플래그 — legacy | four_agent | ab_test"""
from __future__ import annotations

import hashlib
import os


class AgentFeatureFlags:
    """에이전트 피처 플래그

    AGENT_DECISION_MODE:
      legacy     → 기존 단일 ReviewerAgent (기본값)
      four_agent → 4-에이전트 새 방식
      ab_test    → A/B 테스트 (ROLLOUT % 비율로 분기)
    """

    @staticmethod
    def get_mode() -> str:
        return os.getenv("AGENT_DECISION_MODE", "legacy").strip().lower()

    @staticmethod
    def audit_trail_enabled() -> bool:
        raw = os.getenv("AGENT_AUDIT_TRAIL_ENABLED", "true").strip().lower()
        return raw not in ("0", "false", "no", "off")

    @staticmethod
    def is_four_agent_enabled(request_id: str | None = None) -> bool:
        mode = AgentFeatureFlags.get_mode()
        if mode == "legacy":
            return False
        if mode == "four_agent":
            return True
        if mode == "ab_test":
            rollout = int(os.getenv("AGENT_FOUR_AGENT_ROLLOUT", "0") or "0")
            if rollout <= 0:
                return False
            if request_id:
                bucket = int(
                    hashlib.md5(request_id.encode(), usedforsecurity=False).hexdigest(),
                    16,
                ) % 100
                return bucket < rollout
            return False
        return False
