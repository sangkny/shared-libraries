"""``shared.notifications.gateway`` — Push provider 추상화 (E R3 Day 1, 2026-05-14).

Phase 2 백로그 "Real FCM/APNs" 대응. 기존 Expo Push 외에 FCM HTTP v1 과 APNs
HTTP/2 게이트웨이를 직접 호출할 수 있도록 인터페이스를 분리한다.

설계 결정
=========
- **provider 단일** — 한 서비스 instance 는 환경 변수 ``PUSH_PROVIDER`` 로 단 하나
  의 provider 만 사용 (mixing 은 R3 백로그). 디바이스 token 형식이 provider 마다
  다르므로, 운영 시 한 회사가 한 provider 를 정해 클라이언트를 통일한다.
- **lazy import** — ``cryptography`` / ``PyJWT`` 같은 외부 SDK 는 사용 시점에 import.
  Expo 만 쓰는 환경에서는 추가 의존성 불필요.
- **dry-run** — ``http_disabled=True`` 면 모든 게이트웨이가 동일한 ``skipped`` 결과
  반환. 단위 테스트가 네트워크 없이 통과.
- **graceful** — 인증/네트워크 오류 시 전체 batch 가 ``failed`` 로 기록되지만
  예외는 전파되지 않는다 (호출자가 best-effort 라고 가정).
- **token cache** — FCM 의 OAuth2 access token 은 1 시간 캐시 (in-process). APNs
  의 provider JWT 은 1 시간 캐시.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from .config import PushConfig


log = logging.getLogger("notifications.gateway")


# ── 공통 결과 타입 ───────────────────────────────────────────────────


@dataclass
class PushMessage:
    """단일 디바이스에 보낼 메시지 (provider 중립)."""

    token: str
    title: str
    body: str
    data: dict[str, Any] = field(default_factory=dict)
    platform: str | None = None  # ios | android | web | unknown


@dataclass
class PushSendResult:
    """배치 발송 결과. ``send_to_user`` 가 반환 dict 로 매핑."""

    sent: int = 0
    failed: int = 0
    skipped: int = 0
    tokens: list[str] = field(default_factory=list)
    detail: str | None = None  # 오류 메시지 / 진단용

    def merge(self, other: "PushSendResult") -> "PushSendResult":
        return PushSendResult(
            sent=self.sent + other.sent,
            failed=self.failed + other.failed,
            skipped=self.skipped + other.skipped,
            tokens=self.tokens + other.tokens,
            detail=self.detail or other.detail,
        )


class PushGateway(Protocol):
    """Provider 별 게이트웨이 인터페이스.

    구현체는 ``send_batch`` 만 채우면 된다. ``http_disabled`` 면 무조건
    ``skipped=len(messages)`` 로 dry-run 결과를 반환해야 한다 (그래야 테스트가
    네트워크 없이 결과를 검증할 수 있다).
    """

    provider: str

    async def send_batch(self, messages: list[PushMessage]) -> PushSendResult: ...


# ── Expo Push (현행 default) ─────────────────────────────────────────


class ExpoPushGateway:
    """Expo Push (`https://exp.host/--/api/v2/push/send`) — 토큰 형식 ``ExponentPushToken[...]``."""

    provider = "expo"

    def __init__(self, config: PushConfig) -> None:
        self.config = config

    async def send_batch(self, messages: list[PushMessage]) -> PushSendResult:
        tokens = [m.token for m in messages]
        if not tokens:
            return PushSendResult()
        if self.config.http_disabled:
            return PushSendResult(skipped=len(tokens), tokens=tokens)

        payload = [
            {
                "to": m.token,
                "title": m.title,
                "body": m.body,
                "data": m.data or {},
                "sound": "default",
            }
            for m in messages
        ]
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.config.expo_access_token:
            headers["Authorization"] = f"Bearer {self.config.expo_access_token}"

        import httpx
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.post(self.config.api_url, json=payload, headers=headers)
            ok = 200 <= resp.status_code < 300
            if ok:
                return PushSendResult(sent=len(tokens), tokens=tokens)
            return PushSendResult(
                failed=len(tokens),
                tokens=tokens,
                detail=f"expo_http_{resp.status_code}: {resp.text[:200]}",
            )
        except Exception as exc:
            log.exception("expo_send_failed err=%s", exc)
            return PushSendResult(
                failed=len(tokens), tokens=tokens, detail=f"expo_exc: {exc}"[:300]
            )


# ── FCM HTTP v1 ─────────────────────────────────────────────────────


_FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
_FCM_TOKEN_TTL = 3500  # 1h - 100s 의 마진


class FCMPushGateway:
    """Firebase Cloud Messaging HTTP v1 API.

    Service Account JSON 으로 RS256 JWT 를 만들고 Google OAuth2 token endpoint
    에서 access_token 을 얻어 ``projects/{pid}/messages:send`` 를 호출한다.

    한계: v1 은 단건 발송 (배치 API 는 legacy). 메시지마다 1 회 HTTP. 본
    구현은 순차 발송 (작은 규모용). 성능이 필요하면 ``asyncio.gather`` 로 풀
    링 가능.
    """

    provider = "fcm"

    def __init__(self, config: PushConfig) -> None:
        self.config = config
        self._cached_token: tuple[str, float] | None = None  # (access_token, exp_ts)

    async def _get_access_token(self) -> str:
        if self._cached_token is not None:
            tok, exp_ts = self._cached_token
            if exp_ts > time.time() + 60:
                return tok

        try:
            import jwt  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - 환경 의존
            raise RuntimeError(
                "FCM 사용 시 PyJWT 가 필요합니다 (pip install pyjwt[crypto])"
            ) from exc

        sa = self._load_service_account()
        now = int(time.time())
        claims = {
            "iss": sa["client_email"],
            "scope": _FCM_SCOPE,
            "aud": sa.get("token_uri", "https://oauth2.googleapis.com/token"),
            "iat": now,
            "exp": now + _FCM_TOKEN_TTL,
        }
        assertion = jwt.encode(
            claims, sa["private_key"], algorithm="RS256",
            headers={"kid": sa.get("private_key_id", "")},
        )
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                sa.get("token_uri", "https://oauth2.googleapis.com/token"),
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": assertion,
                },
            )
        if resp.status_code >= 300:
            raise RuntimeError(
                f"FCM OAuth2 토큰 발급 실패: {resp.status_code} {resp.text[:200]}"
            )
        data = resp.json()
        token = data["access_token"]
        exp_in = int(data.get("expires_in", _FCM_TOKEN_TTL))
        self._cached_token = (token, time.time() + min(exp_in, _FCM_TOKEN_TTL))
        return token

    def _load_service_account(self) -> dict[str, Any]:
        raw = self.config.fcm_service_account_json or ""
        if not raw:
            raise RuntimeError("FCM_SERVICE_ACCOUNT_JSON 미설정")
        raw_stripped = raw.strip()
        if raw_stripped.startswith("{"):
            return json.loads(raw_stripped)
        with open(raw_stripped, "r", encoding="utf-8") as f:
            return json.load(f)

    async def send_batch(self, messages: list[PushMessage]) -> PushSendResult:
        tokens = [m.token for m in messages]
        if not tokens:
            return PushSendResult()
        if self.config.http_disabled:
            return PushSendResult(skipped=len(tokens), tokens=tokens)
        if not self.config.fcm_project_id:
            return PushSendResult(
                failed=len(tokens), tokens=tokens, detail="fcm_project_id 미설정"
            )

        try:
            access_token = await self._get_access_token()
        except Exception as exc:
            log.exception("fcm_oauth_failed err=%s", exc)
            return PushSendResult(
                failed=len(tokens), tokens=tokens, detail=f"fcm_oauth: {exc}"[:300],
            )

        endpoint = (
            f"https://fcm.googleapis.com/v1/projects/"
            f"{self.config.fcm_project_id}/messages:send"
        )
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=utf-8",
        }

        import httpx

        sent = 0
        failed = 0
        last_detail: str | None = None
        async with httpx.AsyncClient(timeout=10.0) as client:
            for m in messages:
                body = {
                    "message": {
                        "token": m.token,
                        "notification": {"title": m.title, "body": m.body},
                        "data": {k: str(v) for k, v in (m.data or {}).items()},
                    }
                }
                try:
                    resp = await client.post(endpoint, json=body, headers=headers)
                    if 200 <= resp.status_code < 300:
                        sent += 1
                    else:
                        failed += 1
                        last_detail = f"fcm_http_{resp.status_code}: {resp.text[:200]}"
                except Exception as exc:
                    failed += 1
                    last_detail = f"fcm_exc: {exc}"[:300]
        return PushSendResult(sent=sent, failed=failed, tokens=tokens, detail=last_detail)


# ── APNs HTTP/2 ────────────────────────────────────────────────────


_APNS_PROD = "https://api.push.apple.com"
_APNS_SANDBOX = "https://api.sandbox.push.apple.com"
_APNS_JWT_TTL = 3300  # < 1h (Apple 권장)


class APNsPushGateway:
    """Apple Push Notification service (HTTP/2 + ES256 provider JWT).

    P8 키 (text) + team_id + key_id 로 provider JWT 를 만들고 ``api.push.apple.com``
    (또는 sandbox) 에 메시지마다 1 요청. ``apns-topic`` 헤더는 bundle_id.

    한계: HTTP/2 multiplexing 은 httpx 가 ``h2`` extras 가 설치되어 있을 때만
    동작. 없으면 HTTP/1.1 로 동작하며 여전히 정상 발송된다.
    """

    provider = "apns"

    def __init__(self, config: PushConfig) -> None:
        self.config = config
        self._cached_jwt: tuple[str, float] | None = None  # (jwt, issued_at)

    def _get_jwt(self) -> str:
        if self._cached_jwt is not None:
            tok, iat = self._cached_jwt
            if (time.time() - iat) < _APNS_JWT_TTL:
                return tok

        try:
            import jwt  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - 환경 의존
            raise RuntimeError(
                "APNs 사용 시 PyJWT[crypto] 가 필요합니다"
            ) from exc

        if not (self.config.apns_team_id and self.config.apns_key_id and self.config.apns_p8):
            raise RuntimeError("APNs 키/팀 ID 미설정")

        p8 = self.config.apns_p8.strip()
        if not p8.startswith("-----BEGIN"):
            with open(p8, "r", encoding="utf-8") as f:
                p8 = f.read()

        now = int(time.time())
        token = jwt.encode(
            {"iss": self.config.apns_team_id, "iat": now},
            p8,
            algorithm="ES256",
            headers={"kid": self.config.apns_key_id, "alg": "ES256"},
        )
        self._cached_jwt = (token, float(now))
        return token

    async def send_batch(self, messages: list[PushMessage]) -> PushSendResult:
        tokens = [m.token for m in messages]
        if not tokens:
            return PushSendResult()
        if self.config.http_disabled:
            return PushSendResult(skipped=len(tokens), tokens=tokens)
        if not self.config.apns_bundle_id:
            return PushSendResult(
                failed=len(tokens), tokens=tokens, detail="apns_bundle_id 미설정"
            )

        try:
            provider_jwt = self._get_jwt()
        except Exception as exc:
            log.exception("apns_jwt_failed err=%s", exc)
            return PushSendResult(
                failed=len(tokens), tokens=tokens, detail=f"apns_jwt: {exc}"[:300],
            )

        base = _APNS_SANDBOX if self.config.apns_use_sandbox else _APNS_PROD
        headers = {
            "authorization": f"bearer {provider_jwt}",
            "apns-topic": self.config.apns_bundle_id,
            "apns-push-type": "alert",
        }

        import httpx

        sent = 0
        failed = 0
        last_detail: str | None = None
        try:
            client_kwargs: dict[str, Any] = {"timeout": 10.0}
            try:
                client_kwargs["http2"] = True  # h2 extras 있으면 HTTP/2
            except Exception:
                pass
            async with httpx.AsyncClient(**client_kwargs) as client:
                for m in messages:
                    body = {
                        "aps": {"alert": {"title": m.title, "body": m.body}},
                        **{k: str(v) for k, v in (m.data or {}).items()},
                    }
                    try:
                        resp = await client.post(
                            f"{base}/3/device/{m.token}", json=body, headers=headers,
                        )
                        if 200 <= resp.status_code < 300:
                            sent += 1
                        else:
                            failed += 1
                            last_detail = (
                                f"apns_http_{resp.status_code}: {resp.text[:200]}"
                            )
                    except Exception as exc:
                        failed += 1
                        last_detail = f"apns_exc: {exc}"[:300]
        except Exception as exc:
            failed = len(tokens) - sent
            last_detail = f"apns_client: {exc}"[:300]
        return PushSendResult(sent=sent, failed=failed, tokens=tokens, detail=last_detail)


# ── factory ─────────────────────────────────────────────────────────


def make_gateway(config: PushConfig) -> PushGateway:
    """``config.provider`` 에 따라 게이트웨이 반환. 기본은 Expo."""
    p = (config.provider or "expo").lower()
    if p == "fcm":
        return FCMPushGateway(config)
    if p == "apns":
        return APNsPushGateway(config)
    return ExpoPushGateway(config)


__all__ = [
    "PushMessage",
    "PushSendResult",
    "PushGateway",
    "ExpoPushGateway",
    "FCMPushGateway",
    "APNsPushGateway",
    "make_gateway",
]
