"""구조화 로깅 + 메트릭 (패키지명 `logging` 은 표준 라이브러리와 충돌하므로 `observability` 사용)."""

from .fastapi_install import install_observability
from .metrics_api import router as metrics_router
from .structured_logger import configure_json_logging

__all__ = [
    "configure_json_logging",
    "install_observability",
    "metrics_router",
]
