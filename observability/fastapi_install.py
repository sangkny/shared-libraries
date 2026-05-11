"""FastAPI 앱에 구조화 로깅 + HTTP 메트릭 + /metrics 라우터 장착.

Step 4 (book §16.10.3 / §16.12.2): 기존 ``/metrics`` JSON 엔드포인트를 보존하고,
Prometheus 텍스트 포맷 전용 ``/metrics/prometheus`` 엔드포인트를 별도 등록한다.
이로써 health-aggregator 등 기존 호출자의 거동을 깨지 않으면서 4개 서비스의
``chunking_*`` 표준 메트릭을 ``service`` 라벨로 cross-service 비교할 수 있다.
"""
from __future__ import annotations

from fastapi import APIRouter, FastAPI
from fastapi.responses import Response

from .metrics_api import router as metrics_router, set_service_name
from .prom_metrics import render_prometheus_text
from .request_middleware import ObservabilityMiddleware
from .structured_logger import configure_json_logging


def _build_prometheus_router() -> APIRouter:
    """``GET /metrics/prometheus`` — Prometheus scrape 전용 텍스트 응답."""
    router = APIRouter()

    @router.get("/metrics/prometheus")
    async def metrics_prometheus() -> Response:
        body, content_type = render_prometheus_text()
        return Response(content=body, media_type=content_type)

    return router


def install_observability(app: FastAPI, service_name: str) -> None:
    configure_json_logging(service_name)
    set_service_name(service_name)
    app.add_middleware(ObservabilityMiddleware, service_name=service_name)
    app.include_router(metrics_router, tags=["metrics"])
    app.include_router(_build_prometheus_router(), tags=["metrics"])
