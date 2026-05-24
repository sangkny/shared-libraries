# shared-libraries/llm — LLM Provider 추상화

설계 SSOT: `shared-libraries-llm-설계문서.docx` · Book [Ch07](../../book/part3/ch07-llm-providers.md)

## 구조

```
llm/
├── __init__.py          # 공개 API
├── base.py              # BaseProvider, ModelRole, LLMRequest/Response
├── client.py            # LLMClient — 팩토리 + Fallback
├── requirements.txt
├── .env.example
├── providers/
│   ├── local.py         # LM Studio (개발 PC TITAN RTX @ 192.168.0.12)
│   ├── openai_provider.py
│   ├── anthropic_provider.py
│   ├── google_provider.py
│   └── azure_provider.py
└── tests/
    └── test_providers.py
```

## ModelRole → Agent 매핑

| Role | 용도 | LOCAL (기본) |
|------|------|----------------|
| FAST | Planner, Generator, Fixer | `google/gemma-4-e4b` |
| HEAVY | Reviewer, Ontology | `google/gemma-4-26b-a4b` |
| VISION | MEDI 안저 | `google/gemma-4-26b-a4b` |
| EMBED | RAG | `text-embedding-nomic-embed-text-v1.5` |
| BACKUP | Fallback | `mistralai/mistral-7b-instruct-v0.3` |

## Provider 5종

| Provider | FAST | HEAVY | 환경변수 |
|----------|------|-------|----------|
| LOCAL | gemma-4-e4b | gemma-4-26b-a4b | `LOCAL_*` |
| OpenAI | gpt-4o-mini | gpt-4o | `OPENAI_*` |
| Anthropic | claude-haiku-4-5 | claude-sonnet-4-6 | `ANTHROPIC_*` |
| Google | gemini-2.0-flash | gemini-1.5-pro | `GOOGLE_*` |
| Azure | 배포별 | 배포별 | `AZURE_*` |

## 사용

```python
from llm import LLMClient, ModelRole

client = LLMClient()  # LLM_PROVIDER=local
resp = await client.chat("안녕", role=ModelRole.FAST)
```

## 테스트

```bash
cd projects/shared-libraries
PYTHONPATH=. python -m pytest llm/tests/test_providers.py -q
```

Docker: `docker exec shared-libs-dev bash -c 'cd /app && PYTHONPATH=. python -m pytest llm/tests/test_providers.py -q'`
