# LLM 설정 가이드 (shared-libraries/llm)

LM Studio 로컬 LLM과 Docker SaaS 서비스 연동 방법.

## §1. LM Studio 설정

1. LM Studio 실행 → **Local Server** 탭
2. 포트: **1234** (기본 Serve 포트)
3. **Serve on Local Network** 활성화
4. 모델 로드: `google/gemma-4-e4b` (FAST), `google/gemma-4-26b-a4b` (HEAVY/VISION)

확인:

```bash
curl http://127.0.0.1:1234/v1/models
```

## §2. WSL에서 접근

개발 PC LAN IP (예: `192.168.0.12`):

```bash
curl http://192.168.0.12:1234/v1/models
```

`.env` 예:

```env
LM_STUDIO_BASE_URL=http://192.168.0.12:1234/v1
LOCAL_BASE_URL=http://192.168.0.12:1234/v1
LLM_PROVIDER=local
```

## §3. Docker에서 접근

컨테이너 → 호스트 LM Studio:

```env
LOCAL_BASE_URL=http://host.docker.internal:1234/v1
LLM_PROVIDER=local
```

`projects/.env.local` 또는 서비스별 `.env`에 설정.

## §4. SVG-Stock 포트 충돌 주의

SVG-Stock 등이 **8000** 포트를 사용하면 LM Studio와 충돌할 수 있습니다.

- LM Studio: **1234** 사용 (권장)
- SVG-Stock 실행 중에는 LM Studio `:8000` 접근 불가 — `:1234`로 통일

## §5. Provider 전환

`LLM_PROVIDER` 환경변수:

| 값 | Provider | 비고 |
|----|----------|------|
| `local` | LM Studio | 개발·테스트 기본 |
| `openai` | OpenAI | `OPENAI_API_KEY` 필요 |
| `anthropic` | Anthropic | 임베딩은 `LLM_EMBED_PROVIDER=local` 권장 |
| `google` | Google Gemini | `GOOGLE_API_KEY` |
| `azure` | Azure OpenAI | 엔터프라이즈 |

예:

```bash
export LLM_PROVIDER=anthropic
export LLM_EMBED_PROVIDER=local
export LOCAL_BASE_URL=http://host.docker.internal:1234/v1
```

템플릿: `llm/.env.example` → `llm/.env` 복사 후 값 입력.

Fallback: `LLM_FALLBACK_ENABLED=true` 시 운영 Provider 실패 시 local 재시도.
