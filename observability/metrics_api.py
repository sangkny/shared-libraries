"""GET /metrics — JSON 스냅샷."""
from __future__ import annotations

from fastapi import APIRouter

from . import metrics_collector

router = APIRouter()
_service_name = "unknown"


def set_service_name(name: str) -> None:
    global _service_name
    _service_name = name


@router.get("/metrics")
async def metrics() -> dict:
    return metrics_collector.snapshot(_service_name)
