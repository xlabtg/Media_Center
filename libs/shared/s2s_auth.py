from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Protocol

import httpx
import jwt
from fastapi import HTTPException, Request, status

S2S_AUTH_METHOD_ENV = "S2S_AUTH_METHOD"
S2S_SHARED_SECRET_ENV = "S2S_SHARED_SECRET"
S2S_AUDIENCE_ENV = "S2S_AUDIENCE"
S2S_SERVICE_NAME_ENV = "SERVICE_NAME"
S2S_REPLAY_WINDOW_SECONDS_ENV = "S2S_REPLAY_WINDOW_SECONDS"
S2S_TOKEN_TTL_SECONDS_ENV = "S2S_TOKEN_TTL_SECONDS"
K8S_AUTH_ENABLED_ENV = "K8S_AUTH_ENABLED"
S2S_K8S_TOKEN_PATH_ENV = "S2S_K8S_TOKEN_PATH"
S2S_K8S_ISSUER_ENV = "S2S_K8S_ISSUER"
S2S_K8S_TOKENREVIEW_URL_ENV = "S2S_K8S_TOKENREVIEW_URL"
S2S_K8S_TOKENREVIEW_TOKEN_PATH_ENV = "S2S_K8S_TOKENREVIEW_TOKEN_PATH"
S2S_K8S_TOKENREVIEW_TIMEOUT_SECONDS_ENV = "S2S_K8S_TOKENREVIEW_TIMEOUT_SECONDS"
S2S_K8S_CA_PATH_ENV = "S2S_K8S_CA_PATH"
S2S_K8S_OIDC_PUBLIC_KEY_PATH_ENV = "S2S_K8S_OIDC_PUBLIC_KEY_PATH"
S2S_RSA_PRIVATE_KEY_PATH_ENV = "S2S_RSA_PRIVATE_KEY_PATH"
S2S_RSA_PUBLIC_KEY_PATH_ENV = "S2S_RSA_PUBLIC_KEY_PATH"
S2S_RSA_ISSUER_ENV = "S2S_RSA_ISSUER"
S2S_RSA_AUDIENCE_ENV = "S2S_RSA_AUDIENCE"

AUTHORIZATION_HEADER = "Authorization"
S2S_METHOD_HEADER = "X-S2S-Method"
S2S_SERVICE_HEADER = "X-S2S-Service"
S2S_TIMESTAMP_HEADER = "X-S2S-Timestamp"
S2S_NONCE_HEADER = "X-S2S-Nonce"
S2S_SIGNATURE_HEADER = "X-S2S-Signature"

DEFAULT_S2S_REPLAY_WINDOW_SECONDS = 300
DEFAULT_S2S_TOKEN_TTL_SECONDS = 60
DEFAULT_S2S_TOKENREVIEW_TIMEOUT_SECONDS = 2.0
DEFAULT_S2S_SERVICE_NAME = "unknown"
DEFAULT_S2S_AUDIENCE = "nmc-services"
DEFAULT_S2S_RSA_ISSUER = "nmc-s2s"
DEFAULT_K8S_SERVICE_ACCOUNT_TOKEN_PATH = Path(
    "/var/run/secrets/kubernetes.io/serviceaccount/token",
)
DEFAULT_K8S_SERVICE_ACCOUNT_CA_PATH = Path(
    "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt",
)
K8S_TOKENREVIEW_PATH = "/apis/authentication.k8s.io/v1/tokenreviews"

Clock = Callable[[], float]


class AuthMethod(StrEnum):
    K8S_SA = "kubernetes_sa"
    RSA_KEY = "rsa_key"
    SHARED_SECRET = "shared_secret"


@dataclass(frozen=True, slots=True)
class S2SConfig:
    shared_secret: str | bytes | None = None
    replay_window_seconds: int = DEFAULT_S2S_REPLAY_WINDOW_SECONDS
    method: AuthMethod | str | None = None
    service_name: str = DEFAULT_S2S_SERVICE_NAME
    k8s_enabled: bool = True
    k8s_token_path: str | Path = DEFAULT_K8S_SERVICE_ACCOUNT_TOKEN_PATH
    k8s_audience: str = DEFAULT_S2S_AUDIENCE
    k8s_issuer: str | None = None
    k8s_tokenreview_url: str | None = None
    k8s_tokenreview_token_path: str | Path | None = None
    k8s_tokenreview_timeout_seconds: float = DEFAULT_S2S_TOKENREVIEW_TIMEOUT_SECONDS
    k8s_ca_path: str | Path | None = DEFAULT_K8S_SERVICE_ACCOUNT_CA_PATH
    k8s_oidc_public_key_path: str | Path | None = None
    rsa_private_key_path: str | Path | None = None
    rsa_public_key_path: str | Path | None = None
    rsa_issuer: str = DEFAULT_S2S_RSA_ISSUER
    rsa_audience: str = DEFAULT_S2S_AUDIENCE
    token_ttl_seconds: int = DEFAULT_S2S_TOKEN_TTL_SECONDS

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "shared_secret",
            _normalize_optional_secret(self.shared_secret),
        )
        object.__setattr__(
            self,
            "replay_window_seconds",
            _normalize_positive_int(
                self.replay_window_seconds,
                "replay_window_seconds",
            ),
        )
        object.__setattr__(
            self,
            "method",
            _normalize_optional_auth_method(self.method),
        )
        object.__setattr__(
            self,
            "service_name",
            _normalize_config_text(self.service_name, "service_name"),
        )
        object.__setattr__(self, "k8s_enabled", bool(self.k8s_enabled))
        object.__setattr__(
            self,
            "k8s_token_path",
            _normalize_required_path(self.k8s_token_path, "k8s_token_path"),
        )
        object.__setattr__(
            self,
            "k8s_audience",
            _normalize_config_text(self.k8s_audience, "k8s_audience"),
        )
        object.__setattr__(
            self,
            "k8s_issuer",
            _normalize_optional_config_text(self.k8s_issuer),
        )
        object.__setattr__(
            self,
            "k8s_tokenreview_url",
            _normalize_optional_config_text(self.k8s_tokenreview_url),
        )
        object.__setattr__(
            self,
            "k8s_tokenreview_token_path",
            _normalize_optional_path(self.k8s_tokenreview_token_path),
        )
        object.__setattr__(
            self,
            "k8s_tokenreview_timeout_seconds",
            _normalize_positive_float(
                self.k8s_tokenreview_timeout_seconds,
                "k8s_tokenreview_timeout_seconds",
            ),
        )
        object.__setattr__(
            self,
            "k8s_ca_path",
            _normalize_optional_path(self.k8s_ca_path),
        )
        object.__setattr__(
            self,
            "k8s_oidc_public_key_path",
            _normalize_optional_path(self.k8s_oidc_public_key_path),
        )
        object.__setattr__(
            self,
            "rsa_private_key_path",
            _normalize_optional_path(self.rsa_private_key_path),
        )
        object.__setattr__(
            self,
            "rsa_public_key_path",
            _normalize_optional_path(self.rsa_public_key_path),
        )
        object.__setattr__(
            self,
            "rsa_issuer",
            _normalize_config_text(self.rsa_issuer, "rsa_issuer"),
        )
        object.__setattr__(
            self,
            "rsa_audience",
            _normalize_config_text(self.rsa_audience, "rsa_audience"),
        )
        object.__setattr__(
            self,
            "token_ttl_seconds",
            _normalize_positive_int(self.token_ttl_seconds, "token_ttl_seconds"),
        )

    @classmethod
    def from_env(cls) -> S2SConfig:
        config = _s2s_config_from_env()
        return replace(config, method=detect_auth_method(config))


@dataclass(frozen=True, slots=True)
class S2SIdentity:
    service_name: str
    method: AuthMethod


@dataclass(frozen=True, slots=True)
class K8sTokenReviewResult:
    service_name: str
    audience: str
    issuer: str | None = None
    claims: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "service_name",
            _normalize_config_text(self.service_name, "service_name"),
        )
        object.__setattr__(
            self,
            "audience",
            _normalize_config_text(self.audience, "audience"),
        )
        object.__setattr__(
            self,
            "issuer",
            _normalize_optional_config_text(self.issuer),
        )
        object.__setattr__(self, "claims", dict(self.claims))


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


class K8sTokenValidator(Protocol):
    def __call__(
        self,
        token: str,
        config: S2SConfig,
    ) -> K8sTokenReviewResult: ...


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


def detect_auth_method(config: S2SConfig | None = None) -> AuthMethod:
    resolved_config = config if config is not None else _s2s_config_from_env()
    explicit_method = _normalize_optional_auth_method(resolved_config.method)
    if explicit_method is not None:
        return explicit_method
    if (
        resolved_config.k8s_enabled
        and _as_path(resolved_config.k8s_token_path).is_file()
    ):
        return AuthMethod.K8S_SA
    if (
        resolved_config.rsa_private_key_path is not None
        and _as_path(resolved_config.rsa_private_key_path).is_file()
        and resolved_config.rsa_public_key_path is not None
        and _as_path(resolved_config.rsa_public_key_path).is_file()
    ):
        return AuthMethod.RSA_KEY

    return AuthMethod.SHARED_SECRET


class K8sS2SAuth:
    def __init__(
        self,
        config: S2SConfig | None = None,
        *,
        replay_cache: InMemoryS2SReplayCache | None = None,
        clock: Clock | None = None,
        token_validator: K8sTokenValidator | None = None,
    ) -> None:
        self._config = config or S2SConfig.from_env()
        self._replay_cache = replay_cache or InMemoryS2SReplayCache()
        self._clock = time.time if clock is None else clock
        self._token_validator = token_validator or _default_k8s_token_validator

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
        _normalize_method(method)
        _normalize_path(path)
        normalized_service = _normalize_required_text(service_name, "service_name")
        resolved_timestamp = timestamp if timestamp is not None else int(self._now())
        resolved_nonce = nonce or secrets.token_urlsafe(24)
        normalized_nonce = _normalize_required_text(resolved_nonce, "nonce")
        token = _read_text_file(
            _as_path(self._config.k8s_token_path),
            "k8s_token_path",
        )
        signed_headers = dict(headers or {})
        signed_headers[AUTHORIZATION_HEADER] = f"Bearer {token}"
        signed_headers[S2S_METHOD_HEADER] = AuthMethod.K8S_SA.value
        signed_headers[S2S_SERVICE_HEADER] = normalized_service
        signed_headers[S2S_TIMESTAMP_HEADER] = str(resolved_timestamp)
        signed_headers[S2S_NONCE_HEADER] = normalized_nonce
        return signed_headers

    def verify_request(
        self,
        headers: Mapping[str, str],
        *,
        method: str,
        path: str,
    ) -> S2SIdentity:
        _normalize_method(method)
        _normalize_path(path)
        normalized_headers = _normalize_headers(headers)
        auth_method = normalized_headers.get(S2S_METHOD_HEADER.lower())
        if auth_method != AuthMethod.K8S_SA.value:
            raise S2SAuthenticationError("S2S method не поддерживается")

        token = _bearer_token(normalized_headers)
        nonce = _normalize_required_text(
            normalized_headers.get(S2S_NONCE_HEADER.lower()),
            S2S_NONCE_HEADER,
        )
        timestamp = _parse_timestamp(
            normalized_headers.get(S2S_TIMESTAMP_HEADER.lower()),
        )
        review = self._token_validator(token, self._config)
        if review.audience != self._config.k8s_audience:
            raise S2SAuthenticationError("S2S k8s audience недействителен")
        if (
            self._config.k8s_issuer is not None
            and review.issuer != self._config.k8s_issuer
        ):
            raise S2SAuthenticationError("S2S k8s issuer недействителен")

        self._remember_nonce(
            service_name=review.service_name,
            nonce=nonce,
            timestamp=timestamp,
        )
        return S2SIdentity(
            service_name=review.service_name,
            method=AuthMethod.K8S_SA,
        )

    def _remember_nonce(
        self,
        *,
        service_name: str,
        nonce: str,
        timestamp: int,
    ) -> None:
        _remember_verified_nonce(
            replay_cache=self._replay_cache,
            config=self._config,
            service_name=service_name,
            nonce=nonce,
            timestamp=timestamp,
            now=self._now(),
        )

    def _now(self) -> float:
        return float(self._clock())


class OIDCK8sTokenValidator:
    def __call__(
        self,
        token: str,
        config: S2SConfig,
    ) -> K8sTokenReviewResult:
        if config.k8s_oidc_public_key_path is None:
            raise S2SAuthenticationError("S2S k8s OIDC public key не настроен")
        if config.k8s_issuer is None:
            raise S2SAuthenticationError("S2S k8s issuer не настроен")

        public_key = _read_bytes_file(
            _as_path(config.k8s_oidc_public_key_path),
            "k8s_oidc_public_key_path",
        )
        try:
            claims = jwt.decode(
                token,
                public_key,
                algorithms=["RS256"],
                audience=config.k8s_audience,
                issuer=config.k8s_issuer,
            )
        except jwt.InvalidIssuerError as exc:
            raise S2SAuthenticationError(
                "S2S k8s issuer недействителен",
            ) from exc
        except jwt.InvalidAudienceError as exc:
            raise S2SAuthenticationError(
                "S2S k8s audience недействителен",
            ) from exc
        except jwt.PyJWTError as exc:
            raise S2SAuthenticationError("S2S k8s token недействителен") from exc

        claims_mapping = _ensure_claims_mapping(claims)
        return K8sTokenReviewResult(
            service_name=_k8s_service_name_from_claims(claims_mapping),
            audience=config.k8s_audience,
            issuer=_claim_required_text(claims_mapping, "iss"),
            claims=claims_mapping,
        )


@dataclass(frozen=True, slots=True)
class TokenReviewK8sTokenValidator:
    transport: httpx.BaseTransport | None = None

    def __call__(
        self,
        token: str,
        config: S2SConfig,
    ) -> K8sTokenReviewResult:
        url = _k8s_tokenreview_url(config)
        reviewer_token_path = config.k8s_tokenreview_token_path or config.k8s_token_path
        reviewer_token = _read_text_file(
            _as_path(reviewer_token_path),
            "k8s_tokenreview_token_path",
        )
        payload = {
            "apiVersion": "authentication.k8s.io/v1",
            "kind": "TokenReview",
            "spec": {
                "token": token,
                "audiences": [config.k8s_audience],
            },
        }
        headers = {
            "Authorization": f"Bearer {reviewer_token}",
            "Content-Type": "application/json",
        }
        verify = _k8s_tokenreview_verify(config)
        try:
            if self.transport is None:
                with httpx.Client(
                    timeout=config.k8s_tokenreview_timeout_seconds,
                    verify=verify,
                ) as client:
                    response = client.post(url, json=payload, headers=headers)
            else:
                with httpx.Client(
                    transport=self.transport,
                    timeout=config.k8s_tokenreview_timeout_seconds,
                    verify=verify,
                ) as client:
                    response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise S2SAuthenticationError(
                "S2S k8s TokenReview недоступен",
            ) from exc

        return _parse_tokenreview_response(response, token=token, config=config)


class RSAS2SAuth:
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
        normalized_method = _normalize_method(method)
        normalized_path = _normalize_path(path)
        normalized_service = _normalize_required_text(service_name, "service_name")
        resolved_timestamp = timestamp if timestamp is not None else int(self._now())
        resolved_nonce = nonce or secrets.token_urlsafe(24)
        normalized_nonce = _normalize_required_text(resolved_nonce, "nonce")
        claims = {
            "iss": self._config.rsa_issuer,
            "sub": normalized_service,
            "aud": self._config.rsa_audience,
            "iat": resolved_timestamp,
            "nbf": resolved_timestamp - 1,
            "exp": resolved_timestamp + self._config.token_ttl_seconds,
            "nonce": normalized_nonce,
            "method": normalized_method,
            "path": normalized_path,
        }
        token = jwt.encode(
            claims,
            self._private_key_bytes(),
            algorithm="RS256",
        )
        signed_headers = dict(headers or {})
        signed_headers[AUTHORIZATION_HEADER] = f"Bearer {token}"
        signed_headers[S2S_METHOD_HEADER] = AuthMethod.RSA_KEY.value
        signed_headers[S2S_SERVICE_HEADER] = normalized_service
        signed_headers[S2S_TIMESTAMP_HEADER] = str(resolved_timestamp)
        signed_headers[S2S_NONCE_HEADER] = normalized_nonce
        return signed_headers

    def verify_request(
        self,
        headers: Mapping[str, str],
        *,
        method: str,
        path: str,
    ) -> S2SIdentity:
        normalized_method = _normalize_method(method)
        normalized_path = _normalize_path(path)
        normalized_headers = _normalize_headers(headers)
        auth_method = normalized_headers.get(S2S_METHOD_HEADER.lower())
        if auth_method != AuthMethod.RSA_KEY.value:
            raise S2SAuthenticationError("S2S method не поддерживается")

        token = _bearer_token(normalized_headers)
        claims = self._decode_token(token)
        service_name = _claim_required_text(claims, "sub")
        nonce = _normalize_required_text(
            normalized_headers.get(S2S_NONCE_HEADER.lower()),
            S2S_NONCE_HEADER,
        )
        timestamp = _parse_timestamp(
            normalized_headers.get(S2S_TIMESTAMP_HEADER.lower()),
        )
        if _claim_required_int(claims, "iat") != timestamp:
            raise S2SAuthenticationError("S2S RSA timestamp не совпадает")
        if _claim_required_text(claims, "nonce") != nonce:
            raise S2SAuthenticationError("S2S RSA nonce не совпадает")
        if _claim_required_text(claims, "method") != normalized_method:
            raise S2SAuthenticationError("S2S RSA method не совпадает")
        if _claim_required_text(claims, "path") != normalized_path:
            raise S2SAuthenticationError("S2S RSA path не совпадает")

        _remember_verified_nonce(
            replay_cache=self._replay_cache,
            config=self._config,
            service_name=service_name,
            nonce=nonce,
            timestamp=timestamp,
            now=self._now(),
        )
        return S2SIdentity(service_name=service_name, method=AuthMethod.RSA_KEY)

    def _decode_token(self, token: str) -> Mapping[str, object]:
        try:
            claims = jwt.decode(
                token,
                self._public_key_bytes(),
                algorithms=["RS256"],
                audience=self._config.rsa_audience,
                issuer=self._config.rsa_issuer,
            )
        except jwt.InvalidIssuerError as exc:
            raise S2SAuthenticationError(
                "S2S RSA issuer недействителен",
            ) from exc
        except jwt.InvalidAudienceError as exc:
            raise S2SAuthenticationError(
                "S2S RSA audience недействителен",
            ) from exc
        except jwt.PyJWTError as exc:
            raise S2SAuthenticationError("S2S RSA token недействителен") from exc

        return _ensure_claims_mapping(claims)

    def _private_key_bytes(self) -> bytes:
        if self._config.rsa_private_key_path is None:
            raise S2SAuthenticationError("S2S RSA private key не настроен")

        return _read_bytes_file(
            _as_path(self._config.rsa_private_key_path),
            "rsa_private_key_path",
        )

    def _public_key_bytes(self) -> bytes:
        if self._config.rsa_public_key_path is None:
            raise S2SAuthenticationError("S2S RSA public key не настроен")

        return _read_bytes_file(
            _as_path(self._config.rsa_public_key_path),
            "rsa_public_key_path",
        )

    def _now(self) -> float:
        return float(self._clock())


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


def get_s2s_auth(config: S2SConfig | None = None) -> S2SAuthenticator:
    resolved_config = config or S2SConfig.from_env()
    method = detect_auth_method(resolved_config)
    resolved_config = replace(resolved_config, method=method)
    if method is AuthMethod.K8S_SA:
        return K8sS2SAuth(resolved_config)
    if method is AuthMethod.RSA_KEY:
        return RSAS2SAuth(resolved_config)

    return SharedSecretS2SAuth(resolved_config)


def require_s2s(request: Request) -> S2SIdentity:
    cached_identity = getattr(request.state, "s2s_identity", None)
    if isinstance(cached_identity, S2SIdentity):
        return cached_identity

    authenticator = getattr(request.app.state, "s2s_auth", None)
    if authenticator is None:
        authenticator = get_s2s_auth()

    try:
        identity = authenticator.verify_request(
            request.headers,
            method=request.method,
            path=request.url.path,
        )
    except S2SAuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc

    request.state.s2s_identity = identity
    return identity


def _default_k8s_token_validator(
    token: str,
    config: S2SConfig,
) -> K8sTokenReviewResult:
    if config.k8s_oidc_public_key_path is not None:
        return OIDCK8sTokenValidator()(token, config)

    return TokenReviewK8sTokenValidator()(token, config)


def _s2s_config_from_env() -> S2SConfig:
    return S2SConfig(
        method=os.environ.get(S2S_AUTH_METHOD_ENV),
        shared_secret=os.environ.get(S2S_SHARED_SECRET_ENV),
        replay_window_seconds=_env_int(
            S2S_REPLAY_WINDOW_SECONDS_ENV,
            DEFAULT_S2S_REPLAY_WINDOW_SECONDS,
        ),
        service_name=os.environ.get(
            S2S_SERVICE_NAME_ENV,
            DEFAULT_S2S_SERVICE_NAME,
        ),
        k8s_enabled=_env_bool(K8S_AUTH_ENABLED_ENV, default=True),
        k8s_token_path=os.environ.get(
            S2S_K8S_TOKEN_PATH_ENV,
            str(DEFAULT_K8S_SERVICE_ACCOUNT_TOKEN_PATH),
        ),
        k8s_audience=os.environ.get(S2S_AUDIENCE_ENV, DEFAULT_S2S_AUDIENCE),
        k8s_issuer=os.environ.get(S2S_K8S_ISSUER_ENV),
        k8s_tokenreview_url=os.environ.get(S2S_K8S_TOKENREVIEW_URL_ENV),
        k8s_tokenreview_token_path=os.environ.get(
            S2S_K8S_TOKENREVIEW_TOKEN_PATH_ENV,
        ),
        k8s_tokenreview_timeout_seconds=_env_float(
            S2S_K8S_TOKENREVIEW_TIMEOUT_SECONDS_ENV,
            DEFAULT_S2S_TOKENREVIEW_TIMEOUT_SECONDS,
        ),
        k8s_ca_path=os.environ.get(
            S2S_K8S_CA_PATH_ENV,
            str(DEFAULT_K8S_SERVICE_ACCOUNT_CA_PATH),
        ),
        k8s_oidc_public_key_path=os.environ.get(
            S2S_K8S_OIDC_PUBLIC_KEY_PATH_ENV,
        ),
        rsa_private_key_path=os.environ.get(S2S_RSA_PRIVATE_KEY_PATH_ENV),
        rsa_public_key_path=os.environ.get(S2S_RSA_PUBLIC_KEY_PATH_ENV),
        rsa_issuer=os.environ.get(S2S_RSA_ISSUER_ENV, DEFAULT_S2S_RSA_ISSUER),
        rsa_audience=os.environ.get(S2S_RSA_AUDIENCE_ENV, DEFAULT_S2S_AUDIENCE),
        token_ttl_seconds=_env_int(
            S2S_TOKEN_TTL_SECONDS_ENV,
            DEFAULT_S2S_TOKEN_TTL_SECONDS,
        ),
    )


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


def _normalize_optional_auth_method(
    value: AuthMethod | str | None,
) -> AuthMethod | None:
    if value is None:
        return None
    if isinstance(value, AuthMethod):
        return value

    normalized = value.strip().lower().replace("-", "_")
    if normalized == "":
        return None
    aliases = {
        "k8s": AuthMethod.K8S_SA,
        "k8s_sa": AuthMethod.K8S_SA,
        "kubernetes": AuthMethod.K8S_SA,
        "kubernetes_sa": AuthMethod.K8S_SA,
        "rsa": AuthMethod.RSA_KEY,
        "rsa_key": AuthMethod.RSA_KEY,
        "shared": AuthMethod.SHARED_SECRET,
        "secret": AuthMethod.SHARED_SECRET,
        "shared_secret": AuthMethod.SHARED_SECRET,
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise ValueError(f"неизвестный S2S auth method: {value}") from exc


def _normalize_positive_int(value: int, field_name: str) -> int:
    if value <= 0:
        raise ValueError(f"{field_name} должен быть положительным")

    return int(value)


def _normalize_positive_float(value: float, field_name: str) -> float:
    if value <= 0:
        raise ValueError(f"{field_name} должен быть положительным")

    return float(value)


def _normalize_required_path(value: str | Path, field_name: str) -> Path:
    path = _normalize_optional_path(value)
    if path is None:
        raise ValueError(f"{field_name} должен быть непустым путём")

    return path


def _normalize_optional_path(value: str | Path | None) -> Path | None:
    if value is None:
        return None
    if isinstance(value, Path):
        return value

    normalized = str(value).strip()
    if normalized == "":
        return None

    return Path(normalized)


def _as_path(value: str | Path) -> Path:
    return value if isinstance(value, Path) else Path(value)


def _normalize_config_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if normalized == "":
        raise ValueError(f"{field_name} должен быть непустой строкой")

    return normalized


def _normalize_optional_config_text(value: str | None) -> str | None:
    if value is None:
        return None

    normalized = value.strip()
    return normalized or None


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


def _bearer_token(normalized_headers: Mapping[str, str]) -> str:
    raw_auth = _normalize_required_text(
        normalized_headers.get(AUTHORIZATION_HEADER.lower()),
        AUTHORIZATION_HEADER,
    )
    prefix = "Bearer "
    if not raw_auth.startswith(prefix):
        raise S2SAuthenticationError("Authorization должен быть Bearer token")

    return _normalize_required_text(raw_auth.removeprefix(prefix), "Bearer token")


def _remember_verified_nonce(
    *,
    replay_cache: InMemoryS2SReplayCache,
    config: S2SConfig,
    service_name: str,
    nonce: str,
    timestamp: int,
    now: float,
) -> None:
    if abs(now - timestamp) > config.replay_window_seconds:
        raise S2SAuthenticationError("S2S timestamp вне допустимого окна")
    replay_cache.remember(
        service_name=service_name,
        nonce=nonce,
        timestamp=timestamp,
        now=now,
        replay_window_seconds=config.replay_window_seconds,
    )


def _read_text_file(path: Path, field_name: str) -> str:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise S2SAuthenticationError(f"{field_name} недоступен") from exc
    if value == "":
        raise S2SAuthenticationError(f"{field_name} пуст")

    return value


def _read_bytes_file(path: Path, field_name: str) -> bytes:
    try:
        value = path.read_bytes()
    except OSError as exc:
        raise S2SAuthenticationError(f"{field_name} недоступен") from exc
    if value.strip() == b"":
        raise S2SAuthenticationError(f"{field_name} пуст")

    return value


def _ensure_claims_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise S2SAuthenticationError("S2S JWT claims недействительны")
    if not all(isinstance(key, str) for key in value):
        raise S2SAuthenticationError("S2S JWT claims недействительны")

    return value


def _claim_required_text(claims: Mapping[str, object], name: str) -> str:
    value = claims.get(name)
    if not isinstance(value, str) or value.strip() == "":
        raise S2SAuthenticationError(f"S2S JWT claim {name} отсутствует")

    return value.strip()


def _claim_required_int(claims: Mapping[str, object], name: str) -> int:
    value = claims.get(name)
    if not isinstance(value, int):
        raise S2SAuthenticationError(f"S2S JWT claim {name} отсутствует")

    return value


def _k8s_service_name_from_claims(claims: Mapping[str, object]) -> str:
    subject = _claim_required_text(claims, "sub")
    return _service_name_from_k8s_username(subject)


def _service_name_from_k8s_username(username: str) -> str:
    parts = username.split(":")
    if len(parts) == 4 and parts[:2] == ["system", "serviceaccount"]:
        return f"{parts[2]}/{parts[3]}"

    return _normalize_config_text(username, "service_name")


def _k8s_tokenreview_url(config: S2SConfig) -> str:
    if config.k8s_tokenreview_url is not None:
        return config.k8s_tokenreview_url

    host = os.environ.get("KUBERNETES_SERVICE_HOST")
    if host is None or host.strip() == "":
        raise S2SAuthenticationError("S2S k8s TokenReview URL не настроен")
    port = (
        os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS")
        or os.environ.get("KUBERNETES_SERVICE_PORT")
        or "443"
    )
    return f"https://{host.strip()}:{port.strip()}{K8S_TOKENREVIEW_PATH}"


def _k8s_tokenreview_verify(config: S2SConfig) -> bool | str:
    ca_path = _as_path(config.k8s_ca_path) if config.k8s_ca_path is not None else None
    if ca_path is not None and ca_path.is_file():
        return str(ca_path)

    return True


def _parse_tokenreview_response(
    response: httpx.Response,
    *,
    token: str,
    config: S2SConfig,
) -> K8sTokenReviewResult:
    try:
        payload = response.json()
    except ValueError as exc:
        raise S2SAuthenticationError("S2S k8s TokenReview вернул не JSON") from exc
    if not isinstance(payload, dict):
        raise S2SAuthenticationError("S2S k8s TokenReview ответ недействителен")

    status_payload = payload.get("status")
    if not isinstance(status_payload, dict):
        raise S2SAuthenticationError("S2S k8s TokenReview status отсутствует")
    if status_payload.get("authenticated") is not True:
        raise S2SAuthenticationError("S2S k8s token не аутентифицирован")

    audiences = _string_list(status_payload.get("audiences"))
    if config.k8s_audience not in audiences:
        raise S2SAuthenticationError("S2S k8s audience недействителен")

    username = _tokenreview_username(status_payload)
    claims = _decode_unverified_claims(token)
    issuer = _issuer_from_claims(claims)
    if config.k8s_issuer is not None and issuer != config.k8s_issuer:
        raise S2SAuthenticationError("S2S k8s issuer недействителен")

    return K8sTokenReviewResult(
        service_name=_service_name_from_k8s_username(username),
        audience=config.k8s_audience,
        issuer=issuer,
        claims=claims,
    )


def _tokenreview_username(status_payload: Mapping[str, object]) -> str:
    user_payload = status_payload.get("user")
    if not isinstance(user_payload, dict):
        raise S2SAuthenticationError("S2S k8s TokenReview user отсутствует")
    username = user_payload.get("username")
    if not isinstance(username, str) or username.strip() == "":
        raise S2SAuthenticationError("S2S k8s TokenReview username отсутствует")

    return username.strip()


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []

    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _decode_unverified_claims(token: str) -> Mapping[str, object]:
    try:
        claims = jwt.decode(
            token,
            options={
                "verify_signature": False,
                "verify_aud": False,
                "verify_iss": False,
            },
        )
    except jwt.PyJWTError:
        return {}

    return _ensure_claims_mapping(claims)


def _issuer_from_claims(claims: Mapping[str, object]) -> str | None:
    issuer = claims.get("iss")
    if isinstance(issuer, str) and issuer.strip():
        return issuer.strip()

    return None


def _env_bool(name: str, *, default: bool) -> bool:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default

    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False

    raise ValueError(f"{name} должен быть bool")


def _env_int(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None or raw_value.strip() == "":
        return default

    try:
        return int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} должен быть int") from exc


def _env_float(name: str, default: float) -> float:
    raw_value = os.environ.get(name)
    if raw_value is None or raw_value.strip() == "":
        return default

    try:
        return float(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} должен быть float") from exc
