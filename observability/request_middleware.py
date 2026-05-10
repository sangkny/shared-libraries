"""FastAPI 요청/응답 시간 기록 + JSON 한 줄 로그."""
from __future__ import annotations

import logging
import time
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from . import metrics_collector

log = logging.getLogger("observability.http")


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """요청당 1회 HTTP 메트릭 + 요약 로그."""

    def __init__(self, app: object, *, service_name: str) -> None:
        super().__init__(app)
        self._service_name = service_name

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        if path in ("/health", "/metrics") or path.startswith("/docs") or path.startswith(
            "/openapi.json"
        ):
            return await call_next(request)

        t0 = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            dt_ms = (time.perf_counter() - t0) * 1000.0
            metrics_collector.record_http_request(dt_ms)
            log.info(
                "http_request",
                extra={
                    "extra_json": {
                        "method":      request.method,
                        "path":        path,
                        "status_code": status_code,
                        "duration_ms": round(dt_ms, 3),
                        "service":     self._service_name,
                    },
                },
            )
