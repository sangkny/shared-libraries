#!/usr/bin/env python3
"""AutoNoGaDaWorkflow LM Studio 실연동 스모크."""
from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("LM_STUDIO_BASE_URL", "http://192.168.0.12:1234/v1")
os.environ.setdefault("LOCAL_BASE_URL", "http://192.168.0.12:1234/v1")
os.environ.setdefault("LLM_PROVIDER", "local")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm.client import LLMClient
from orchestrator.workflow import AutoNoGaDaWorkflow


async def main() -> None:
    client = LLMClient()
    workflow = AutoNoGaDaWorkflow(llm=client)

    print("=== 시나리오 1: 코드 자동화 ===")
    r1 = await workflow.run(
        "Python으로 안저 이미지 파일명에서 patient_id와 eye(OD/OS)를 추출하는 함수를 작성해줘"
    )
    print("passed:", r1.passed, "output:", str(r1.output)[:500])

    print("=== 시나리오 2: 문서 자동화 ===")
    r2 = await workflow.run(
        "DR grade=0, Glaucoma prob=0.605 REVISE, AMD prob=0.12 결과로 환자에게 설명할 간단한 진단 요약을 작성해줘"
    )
    print("passed:", r2.passed, "output:", str(r2.output)[:500])


if __name__ == "__main__":
    asyncio.run(main())
