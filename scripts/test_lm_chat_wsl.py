#!/usr/bin/env python3
"""LM Studio chat 스모크 — WSL에서 Windows LM Studio(8000) 연결 테스트."""
from __future__ import annotations

import asyncio
import os
import sys

# shared-libraries 루트
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.probe_lm_studio import find_lm_studio_url
from llm.client import LLMClient
from llm.base import ModelRole


async def main() -> int:
    base = find_lm_studio_url()
    if not base:
        print("LM Studio 미실행 — http://localhost:8000/v1 확인", file=sys.stderr)
        return 1

    os.environ.setdefault("LOCAL_BASE_URL", base)
    os.environ.setdefault("LM_STUDIO_AVAILABLE", "1")

    client = LLMClient()
    res = await client.chat(
        "한 문장으로 'hello'를 한국어로 번역하세요.",
        role=ModelRole.FAST,
        max_tokens=64,
        temperature=0.0,
    )
    print("base_url:", base)
    print("model:", res.model_used)
    print("reply:", (res.content or "")[:200])
    return 0 if res.content else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
