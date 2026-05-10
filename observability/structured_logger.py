"""
구조화 JSON 로그 — stdout 한 줄 = JSON object (컨테이너 로그 드라이버·Loki 연동 용이).
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Mapping


class JsonFormatter(logging.Formatter):
    """표준 logging 핸들러용 JSON Formatter."""

    def __init__(self, service_name: str) -> None:
        super().__init__()
        self._service = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts":       datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "level":    record.levelname,
            "logger":   record.name,
            "service":  self._service,
            "msg":      record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if getattr(record, "extra_json", None):
            ex = record.extra_json
            if isinstance(ex, Mapping):
                payload["extra"] = dict(ex)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_json_logging(service_name: str, level: int = logging.INFO) -> None:
    """루트 로깅을 JSON 한 줄 포맷으로 설정 (uvicorn 제외 가능 시 추가 조정)."""
    root = logging.getLogger()
    root.handlers.clear()
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter(service_name))
    root.addHandler(h)
    root.setLevel(level)
    # uvicorn / watchfiles 노이즈는 WARNING
    for name in ("uvicorn", "uvicorn.error", "watchfiles", "httpx"):
        logging.getLogger(name).setLevel(logging.WARNING)
