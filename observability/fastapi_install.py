"""FastAPI 앱에 구조화 로깅 + HTTP 메트릭 + /metrics 라우터 장착."""
from __future__ import annotations

from fastapi import FastAPI

from .metrics_api import router as metrics_router, set_service_name
from .request_middleware import ObservabilityMiddleware
from .structured_logger import configure_json_logging


def install_observability(app: FastAPI, service_name: str) -> None:
    configure_json_logging(service_name)
    set_service_name(service_name)
    app.add_middleware(ObservabilityMiddleware, service_name=service_name)
    app.include_router(metrics_router, tags=["metrics"])
