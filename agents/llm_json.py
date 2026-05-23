# agents/llm_json.py
"""LM Studio / OpenAI 응답에서 JSON 객체 추출."""
from __future__ import annotations

import json
import re


def parse_llm_json(content: str) -> dict:
    """마크다운·앞뒤 텍스트가 섞인 LLM 출력에서 JSON dict 추출."""
    text = (content or "").strip()
    if not text:
        raise ValueError("empty LLM response")

    if "```" in text:
        for block in re.findall(r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.I):
            block = block.strip()
            if block.startswith("{"):
                text = block
                break

    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in LLM response")
    return json.loads(text[start : end + 1])
