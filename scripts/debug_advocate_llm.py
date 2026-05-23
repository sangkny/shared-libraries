#!/usr/bin/env python3
import asyncio
import os

os.environ.setdefault("LOCAL_BASE_URL", "http://host.docker.internal:8000/v1")


async def main():
    import json
    from llm.client import LLMClient
    from llm.base import ModelRole
    from agents.llm_json import parse_llm_json

    text = '{"function_name": "safe_divide", "language": "python"}'
    prompt = (
        f"Domain=software. Artifact={text}. "
        'Reply ONE line JSON: {"reasons":["r1","r2","r3"],"standards":["s1"],'
        '"confidence":0.85,"recommendation":"APPROVE","summary":"ok"}'
    )
    client = LLMClient()
    res = await client.chat(
        prompt,
        role=ModelRole.FAST,
        system="JSON만 출력. 코드펜스 없음.",
        max_tokens=600,
        temperature=0.2,
    )
    print("len:", len(res.content or ""))
    print("raw:", repr((res.content or "")[:500]))
    try:
        d = parse_llm_json(res.content)
        print("parsed OK:", d.get("summary"))
    except Exception as e:
        print("parse fail:", e)


asyncio.run(main())
