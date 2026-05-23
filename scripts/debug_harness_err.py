import asyncio
import os
import traceback

os.environ.setdefault("AGENT_FOUR_AGENT_MOCK", "1")
os.environ.setdefault("AGENT_PIPELINE_LITE", "1")

from agents.pipeline import AgentPipeline


async def main():
    p = AgentPipeline()
    for inp, dom in [
        ("IOP=25 안저 데이터", "iot"),
        ("PII 포함 의료 기록", "medical"),
    ]:
        try:
            os.environ["AGENT_DECISION_MODE"] = "legacy"
            r = await p.run(inp, dom, request_id="x")
            print(dom, "legacy", r.decision.decision)
        except Exception:
            print(dom, "legacy ERR")
            traceback.print_exc()


asyncio.run(main())
