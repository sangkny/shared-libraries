"""구조화 로깅 + 메트릭 (패키지명 `logging` 은 표준 라이브러리와 충돌하므로 `observability` 사용).

Step 4 (book §16.10.3) — Prometheus 텍스트 포맷 메트릭이 ``prom_metrics`` 에 추가됨.
기존 ``/metrics`` JSON 엔드포인트는 그대로 보존되며, Prometheus scrape 는
``/metrics/prometheus`` 로 분리되어 cross-service 비교 (Grafana ``service`` 라벨)
가 가능하다.
"""

from .fastapi_install import install_observability
from .metrics_api import router as metrics_router
from .prom_metrics import (
    CONTENT_TYPE_LATEST,
    inc_chunking_counter,
    observe_chunking_compression,
    observe_chunking_duration,
    observe_chunking_snapshot,
    render_prometheus_text,
)
from .structured_logger import configure_json_logging

__all__ = [
    "CONTENT_TYPE_LATEST",
    "configure_json_logging",
    "inc_chunking_counter",
    "install_observability",
    "metrics_router",
    "observe_chunking_compression",
    "observe_chunking_duration",
    "observe_chunking_snapshot",
    "render_prometheus_text",
]
