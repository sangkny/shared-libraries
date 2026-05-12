"""shared-libraries pytest 글로벌 설정 — Step 8 (테스트 철학 정착, 2026-05-12).

CURSOR_HANDOVER §"테스트 철학" 원칙 구현:

1. LM Studio 통합 테스트 게이트 (양쪽 모두 충족해야 실행):
   - 환경변수 ``LM_STUDIO_AVAILABLE=1`` — 개발자가 명시적으로 활성화
   - HTTP ping ``GET LM_STUDIO_BASE_URL/v1/models`` 200 응답
   CI 기본은 두 조건 모두 불충족 → 자동 skip.

2. 마커:
   - ``@pytest.mark.requires_lm_studio`` — integration 테스트의 표준 마커.
     게이트 미통과 시 자동 skip (skip 사유에 환경 차이 명시).

3. CLI 옵션:
   - ``--lm-studio-required`` — 게이트 무시하고 강제 실행.
     실패 시 hard fail (LM Studio 가 떠 있어야 하는 환경에서 회귀 강제).

Mock 사용 절대 금지 (§"테스트 철학" 금지 패턴). 신규 통합 테스트는
``tests/integration/`` 하위에 두고 반드시 본 게이트를 사용한다.
"""
from __future__ import annotations

import logging
import os

import pytest

LOGGER = logging.getLogger("conftest.shared_libs")

_LM_STUDIO_DEFAULT_URL = "http://host.docker.internal:8000/v1"


def _lm_studio_base_url() -> str:
    return os.getenv("LM_STUDIO_BASE_URL", _LM_STUDIO_DEFAULT_URL).rstrip("/")


def _ping_lm_studio(timeout: float = 2.0) -> bool:
    """GET ``{LM_STUDIO_BASE_URL}/models`` 가 200 이면 True.

    의존성: ``httpx``. 미설치/타임아웃/연결실패 → False.
    """
    try:
        import httpx
    except ImportError:
        LOGGER.debug("conftest: httpx 미설치 — LM Studio ping skip")
        return False
    try:
        r = httpx.get(f"{_lm_studio_base_url()}/models", timeout=timeout)
        return r.status_code == 200
    except Exception as exc:
        LOGGER.debug("conftest: LM Studio ping fail (%s)", exc)
        return False


def is_lm_studio_ready() -> bool:
    """LM Studio 통합 테스트 실행 가능 여부 (양쪽 게이트 AND).

    - ``LM_STUDIO_AVAILABLE`` 환경변수가 ``1`` 이어야 한다 (개발자 명시 활성화).
    - 위가 통과해도 실제 HTTP ping 이 200 이어야 한다 (실행 시점 확인).
    """
    if os.getenv("LM_STUDIO_AVAILABLE", "").strip() != "1":
        return False
    return _ping_lm_studio()


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--lm-studio-required",
        action="store_true",
        default=False,
        help=(
            "LM Studio 통합 테스트를 강제 실행. "
            "게이트 (LM_STUDIO_AVAILABLE=1 + ping 200) 미통과 시에도 "
            "skip 하지 않고 그대로 실행 → 자연스러운 fail 유도."
        ),
    )


def pytest_configure(config: pytest.Config) -> None:
    """마커 등록 — pytest 의 unknown marker 경고 방지."""
    config.addinivalue_line(
        "markers",
        (
            "requires_lm_studio: LM Studio /v1 통합 테스트 마커 "
            "(LM_STUDIO_AVAILABLE=1 + ping 200 게이트, "
            "--lm-studio-required 로 강제 실행)"
        ),
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """``requires_lm_studio`` 마커 자동 skip — CI 기본 동작.

    ``--lm-studio-required`` 옵션이 있으면 skip 을 건너뛰고 강제 실행 →
    LM Studio 가 떠 있는 환경에서만 의도되며, 실패 시 자연스러운 fail.
    """
    if config.getoption("--lm-studio-required"):
        return

    if is_lm_studio_ready():
        return

    skip_marker = pytest.mark.skip(
        reason=(
            "LM Studio 통합 테스트 skip — "
            "LM_STUDIO_AVAILABLE=1 + GET /v1/models 200 필요. "
            "(--lm-studio-required 로 강제 실행 가능)"
        )
    )
    for item in items:
        if "requires_lm_studio" in item.keywords:
            item.add_marker(skip_marker)


@pytest.fixture(scope="session")
def lm_studio_available() -> bool:
    """integration fixture — LM Studio 게이트 통과 여부 반환."""
    return is_lm_studio_ready()


@pytest.fixture(scope="session")
def lm_studio_base_url() -> str:
    """integration fixture — LM Studio base URL (env override 가능)."""
    return _lm_studio_base_url()
