from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from fastapi import HTTPException, Request, status

S2S_SHARED_SECRET_ENV = "S2S_SHARED_SECRET"
S2S_METHOD_HEADER = "X-S2S-Method"
S2S_SERVICE_HEADER = "X-S2S-Service"
S2S_TIMESTAMP_HEADER = "X-S2S-Timestamp"
S2S_NONCE_HEADER = "X-S2S-Nonce"
S2S_SIGNATURE_HEADER = "X-S2S-Signature"
DEFAULT_S2S_REPLAY_WINDOW_SECONDS = 300
DEFAULT_S2S_SERVICE_NAME = "unknown"

Clock = Callable[[], float]


class AuthMethod(StrEnum):
    SHARED_SECRET = "shared_secret"


@dataclass(frozen=True, slots=True)
class S2SConfig:
    shared_secret: str | bytes | None = None
    replay_window_seconds: int = DEFAULT_S2S_REPLAY_WINDOW_SECONDS

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "shared_secret",
            _normalize_optional_secret(self.shared_secret),
        )
        object.__setattr__(
            self,
            "replay_window_seconds",
            _normalize_replay_window(self.replay_window_seconds),
        )

    @classmethod
    def from_env(cls) -> S2SConfig:
        return cls(shared_secret=os.environ.get(S2S_SHARED_SECRET_ENV))


@dataclass(frozen=True, slots=True)
class S2SIdentity:
    service_name: str
    method: AuthMethod


class S2SAuthenticationError(Exception):
    pass


class S2SAuthenticator(Protocol):
    def sign_request(
        self,
        *,
        method: str,
        path: str,
        service_name: str = DEFAULT_S2S_SERVICE_NAME,
        headers: Mapping[str, str] | None = None,
        timestamp: int | None = None,
        nonce: str | None = None,
    ) -> dict[str, str]: ...

    def verify_request(
        self,
        headers: Mapping[str, str],
        *,
        method: str,
        path: str,
    ) -> S2SIdentity: ...


@dataclass(slots=True)
class InMemoryS2SReplayCache:
    _seen_nonces: dict[str, float] = field(default_factory=dict)

    def remember(
        self,
        *,
        service_name: str,
        nonce: str,
        timestamp: float,
        now: float,
        replay_window_seconds: int,
    ) -> None:
        self._drop_expired_nonces(
            now=now,
            replay_window_seconds=replay_window_seconds,
        )
        cache_key = f"{service_name}:{nonce}"
        if cache_key in self._seen_nonces:
            raise S2SAuthenticationError("S2S nonce уже использован")

        self._seen_nonces[cache_key] = timestamp

    def _drop_expired_nonces(
        self,
        *,
        now: float,
        replay_window_seconds: int,
    ) -> None:
        expired = [
            nonce
            for nonce, timestamp in self._seen_nonces.items()
            if now - timestamp > replay_window_seconds
        ]
        for nonce in expired:
            del self._seen_nonces[nonce]


class SharedSecretS2SAuth:
    def __init__(
        self,
        config: S2SConfig | None = None,
        *,
        replay_cache: InMemoryS2SReplayCache | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._config = config or S2SConfig.from_env()
        self._replay_cache = replay_cache or InMemoryS2SReplayCache()
        self._clock = time.time if clock is None else clock

    def sign_request(
        self,
        *,
        method: str,
        path: str,
        service_name: str = DEFAULT_S2S_SERVICE_NAME,
        headers: Mapping[str, str] | None = None,
        timestamp: int | None = None,
        nonce: str | None = None,
    ) -> dict[str, str]:
        secret = self._secret_bytes()
        normalized_method = _normalize_method(method)
        normalized_path = _normalize_path(path)
        normalized_service = _normalize_required_text(service_name, "service_name")
        resolved_timestamp = timestamp if timestamp is not None else int(self._now())
        resolved_nonce = nonce or secrets.token_urlsafe(24)
        normalized_nonce = _normalize_required_text(resolved_nonce, "nonce")
        payload = _signature_payload(
            method=normalized_method,
            path=normalized_path,
            timestamp=resolved_timestamp,
            nonce=normalized_nonce,
            service_name=normalized_service,
        )
        signature = hmac.new(
            secret,
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        signed_headers = dict(headers or {})
        signed_headers[S2S_METHOD_HEADER] = AuthMethod.SHARED_SECRET.value
        signed_headers[S2S_SERVICE_HEADER] = normalized_service
        signed_headers[S2S_TIMESTAMP_HEADER] = str(resolved_timestamp)
        signed_headers[S2S_NONCE_HEADER] = normalized_nonce
        signed_headers[S2S_SIGNATURE_HEADER] = signature
        return signed_headers

    def verify_request(
        self,
        headers: Mapping[str, str],
        *,
        method: str,
        path: str,
    ) -> S2SIdentity:
        normalized_headers = _normalize_headers(headers)
        auth_method = normalized_headers.get(S2S_METHOD_HEADER.lower())
        if auth_method != AuthMethod.SHARED_SECRET.value:
            raise S2SAuthenticationError("S2S method не поддерживается")

        service_name = _normalize_required_text(
            normalized_headers.get(S2S_SERVICE_HEADER.lower()),
            S2S_SERVICE_HEADER,
        )
        nonce = _normalize_required_text(
            normalized_headers.get(S2S_NONCE_HEADER.lower()),
            S2S_NONCE_HEADER,
        )
        signature = _normalize_required_text(
            normalized_headers.get(S2S_SIGNATURE_HEADER.lower()),
            S2S_SIGNATURE_HEADER,
        )
        timestamp = _parse_timestamp(
            normalized_headers.get(S2S_TIMESTAMP_HEADER.lower()),
        )
        now = self._now()
        if abs(now - timestamp) > self._config.replay_window_seconds:
            raise S2SAuthenticationError("S2S timestamp вне допустимого окна")

        payload = _signature_payload(
            method=_normalize_method(method),
            path=_normalize_path(path),
            timestamp=timestamp,
            nonce=nonce,
            service_name=service_name,
        )
        expected_signature = hmac.new(
            self._secret_bytes(),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected_signature, signature):
            raise S2SAuthenticationError("S2S signature недействительна")

        self._replay_cache.remember(
            service_name=service_name,
            nonce=nonce,
            timestamp=timestamp,
            now=now,
            replay_window_seconds=self._config.replay_window_seconds,
        )
        return S2SIdentity(
            service_name=service_name,
            method=AuthMethod.SHARED_SECRET,
        )

    def _secret_bytes(self) -> bytes:
        secret = self._config.shared_secret
        if secret is None:
            raise S2SAuthenticationError("S2S shared secret не настроен")

        return secret.encode("utf-8") if isinstance(secret, str) else secret

    def _now(self) -> float:
        return float(self._clock())


def get_s2s_auth(config: S2SConfig | None = None) -> SharedSecretS2SAuth:
    return SharedSecretS2SAuth(config)


def require_s2s(request: Request) -> S2SIdentity:
    authenticator = getattr(request.app.state, "s2s_auth", None)
    if authenticator is None:
        authenticator = get_s2s_auth()

    try:
        return authenticator.verify_request(
            request.headers,
            method=request.method,
            path=request.url.path,
        )
    except S2SAuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc


def _normalize_optional_secret(value: str | bytes | None) -> str | bytes | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        if normalized == "":
            return None
        return normalized
    if value.strip() == b"":
        return None

    return value


def _normalize_replay_window(value: int) -> int:
    if value <= 0:
        raise ValueError("replay_window_seconds должен быть положительным")

    return int(value)


def _normalize_method(value: str) -> str:
    normalized = value.strip().upper()
    if normalized == "":
        raise ValueError("method должен быть непустой строкой")

    return normalized


def _normalize_path(value: str) -> str:
    normalized = value.strip()
    if normalized == "":
        raise ValueError("path должен быть непустой строкой")
    if not normalized.startswith("/"):
        raise ValueError("path должен начинаться с /")

    return normalized


def _normalize_required_text(value: str | None, field_name: str) -> str:
    if value is None:
        raise S2SAuthenticationError(f"{field_name} отсутствует")

    normalized = value.strip()
    if normalized == "":
        raise S2SAuthenticationError(f"{field_name} должен быть непустой строкой")

    return normalized


def _parse_timestamp(value: str | None) -> int:
    raw_timestamp = _normalize_required_text(value, S2S_TIMESTAMP_HEADER)
    try:
        return int(raw_timestamp)
    except ValueError as exc:
        raise S2SAuthenticationError(
            f"{S2S_TIMESTAMP_HEADER} должен быть unix timestamp",
        ) from exc


def _signature_payload(
    *,
    method: str,
    path: str,
    timestamp: int,
    nonce: str,
    service_name: str,
) -> str:
    return "\n".join((method, path, str(timestamp), nonce, service_name))


def _normalize_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {
        name.lower(): value.strip()
        for name, value in headers.items()
        if isinstance(name, str) and isinstance(value, str)
    }
