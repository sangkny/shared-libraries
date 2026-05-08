# shared-libraries/Dockerfile
# autopus-ADK 패턴 참고 — Multi-stage build
# ============================================
# Stage 1: Builder — 의존성 설치
# ============================================
FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ============================================
# Stage 2: Runtime — 최종 이미지
# ============================================
FROM python:3.11-slim AS runtime

RUN apt-get update && apt-get install -y \
    curl git jq ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /root/.local /root/.local

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -e .

ENV \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH=/root/.local/bin:$PATH \
    LLM_PROVIDER=local \
    LLM_FALLBACK_ENABLED=true \
    LOCAL_BASE_URL=http://host.docker.internal:8000/v1 \
    LOCAL_API_KEY=lm-studio \
    LOCAL_FAST_MODEL=google/gemma-4-e4b \
    LOCAL_HEAVY_MODEL=google/gemma-4-26b-a4b \
    LOCAL_VISION_MODEL=google/gemma-4-26b-a4b \
    LOCAL_EMBED_MODEL=text-embedding-nomic-embed-text-v1.5 \
    LOCAL_BACKUP_MODEL=mistralai/mistral-7b-instruct-v0.3 \
    AGENT_MAX_ITERATIONS=3 \
    AGENT_CIRCUIT_BREAKER=3 \
    AGENT_AUTO_LOOP=true \
    LOG_LEVEL=info \
    LOG_FILE=/app/logs/agent.log \
    GIT_AUTHOR_NAME="MEDI-IOT Agent" \
    GIT_AUTHOR_EMAIL="agent@mediiot.local"

RUN mkdir -p /app/logs

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -sf http://host.docker.internal:8000/v1/models \
        | jq -e '.data | length > 0' > /dev/null 2>&1 || exit 1

CMD ["sleep", "infinity"]
