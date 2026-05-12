"""shared-libraries integration tests (Step 8 — 2026-05-12).

CURSOR_HANDOVER §"테스트 철학" 원칙:
    - Mock 절대 금지 — 외부 의존성(LM Studio / DB / 네트워크) 실 호출.
    - 모든 모듈은 ``@pytest.mark.requires_lm_studio`` (또는 동등한 게이트)를
      사용해야 한다. ``../conftest.py`` 의 ``is_lm_studio_ready()`` 가 양쪽
      게이트 (``LM_STUDIO_AVAILABLE=1`` + HTTP ping 200) 를 검증한다.

CI 기본: 두 게이트 모두 불충족 → 자동 skip.
강제 실행: ``pytest --lm-studio-required``.
"""
