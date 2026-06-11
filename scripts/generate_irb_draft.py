#!/usr/bin/env python3
"""IRB 연구계획서 초안 — AutoNoGaDaWorkflow."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

os.environ.setdefault("LM_STUDIO_BASE_URL", "http://192.168.0.12:1234/v1")
os.environ.setdefault("LOCAL_BASE_URL", "http://192.168.0.12:1234/v1")
os.environ.setdefault("LLM_PROVIDER", "local")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from llm.client import LLMClient
from orchestrator.workflow import AutoNoGaDaWorkflow

PROMPT = """
다음 조건으로 IRB 연구계획서 초안을 작성해줘:
- 연구명: AI 기반 안과 진단 보조 소프트웨어 임상 성능 검증
- 대상: 안과 내원 환자 500명
- 질환: DR, 녹내장, AMD, 근시
- 기간: 6개월
- 형식: 한국어, A4 2페이지 분량
"""


async def main() -> None:
    client = LLMClient()
    workflow = AutoNoGaDaWorkflow(llm=client)
    result = await workflow.run(PROMPT.strip())
    text = str(result.output) if result.output else str(result)
    out = Path("/tmp/irb_draft.txt")
    out.write_text(text, encoding="utf-8")
    print(text[:3000])
    print(f"\n저장: {out} ({len(text)} chars) passed={result.passed}")


if __name__ == "__main__":
    asyncio.run(main())
