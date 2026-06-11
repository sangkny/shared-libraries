#!/usr/bin/env python3
"""Partner analyze E2E — MEDI 컨테이너 내부 또는 호스트에서 실행."""
from __future__ import annotations

import base64
import json
import os
import urllib.request
from typing import Any
from urllib.error import HTTPError

BASE = os.getenv("MEDI_URL", "http://127.0.0.1:8000").rstrip("/")


def _post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as exc:
        body = exc.read().decode()
        raise RuntimeError(f"{exc.code} {path}: {body}") from exc


def main() -> None:
    img_path = "/tmp/test_fundus.jpg"
    if not os.path.isfile(img_path):
        from PIL import Image

        Image.new("RGB", (64, 64), (120, 40, 40)).save(img_path)
    b64 = base64.b64encode(open(img_path, "rb").read()).decode()

    import time

    reg = _post(
        "/api/v1/partner/register",
        {
            "partner_id": f"e2e-{int(time.time())}",
            "name": "four-agent-e2e",
            "plan": "trial",
        },
    )
    print("REGISTER:", json.dumps(reg, ensure_ascii=False)[:200])
    pid, key = reg.get("partner_id"), reg.get("api_key")
    body = {
        "partner_id": pid,
        "api_key": key,
        "image_base64": b64,
        "analysis_type": "fundus",
        "return_format": "json",
    }
    out = _post("/api/v1/partner/analyze", body)
    print("\nANALYZE JSON:")
    print(json.dumps(out, ensure_ascii=False, indent=2)[:2000])
    body["return_format"] = "fhir"
    fhir = _post("/api/v1/partner/analyze", body)
    bundle = fhir.get("fhir_bundle") or {}
    print("\nFHIR:", bundle.get("resourceType"), "entries", len(bundle.get("entry") or []))
    print("audit_mode", (fhir.get("audit_trail") or {}).get("mode"))


if __name__ == "__main__":
    main()
