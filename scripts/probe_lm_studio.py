#!/usr/bin/env python3
"""LM Studio 연결 프로브 — WSL/Windows 환경 자동 감지."""
import os
import subprocess
import sys

import httpx

CANDIDATES = [
    os.getenv("LM_STUDIO_BASE_URL", "").rstrip("/"),
    "http://192.168.0.12:1234/v1",
    "http://172.29.192.1:1234/v1",
    "http://127.0.0.1:1234/v1",
    "http://localhost:1234/v1",
    "http://host.docker.internal:1234/v1",
    "http://127.0.0.1:8000/v1",
    "http://localhost:8000/v1",
    "http://host.docker.internal:8000/v1",
    os.getenv("LM_STUDIO_BASE_URL", "").rstrip("/"),
    "http://127.0.0.1:8000/v1",
    "http://localhost:8000/v1",
    "http://host.docker.internal:8000/v1",
]


def _try_httpx(base: str) -> bool:
    try:
        r = httpx.get(f"{base}/models", timeout=5.0)
        return r.status_code == 200 and '"data"' in r.text
    except Exception:
        return False


def _try_windows_curl(base: str) -> bool:
    curl = "/mnt/c/Windows/System32/curl.exe"
    if not os.path.isfile(curl):
        return False
    host_base = base.replace("host.docker.internal", "127.0.0.1")
    try:
        out = subprocess.run(
            [curl, "-s", f"{host_base}/models"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return out.returncode == 0 and '"data"' in (out.stdout or "")
    except Exception:
        return False


def find_lm_studio_url() -> str | None:
    seen: set[str] = set()
    for raw in CANDIDATES:
        if not raw or raw in seen:
            continue
        seen.add(raw)
        if _try_httpx(raw):
            return raw
        if _try_windows_curl(raw):
            return raw.replace("host.docker.internal", "127.0.0.1")
    return None


if __name__ == "__main__":
    url = find_lm_studio_url()
    if url:
        print(url)
        sys.exit(0)
    print("NOT_FOUND", file=sys.stderr)
    sys.exit(1)
