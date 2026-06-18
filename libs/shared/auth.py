from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Protocol
from urllib.parse import quote, urlencode

from libs.shared.tenant import (
    TenantContext,
    UnauthorizedError,
    decode_hs256_jwt,
    encode_hs256_jwt,
    tenant_context_from_claims,
)

Clock = Callable[[], float]
TokenFactory = Callable[[], str]


@dataclass(frozen=True, slots=True)
class TokenPair:
    """Access/refresh pair returned by the authentication boundary."""

    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int
    refresh_expires_in: int


@dataclass(frozen=True, slots=True)
class RefreshTokenRecord:
    """Refresh token metadata with only token hash stored server-side."""

    token_hash: str
    tenant_id: str
    subject: str
    roles: tuple[str, ...]
    refresh_jti: str
    issued_at: int
    expires_at: int
    revoked_at: int | None = None
    replaced_by_jti: str | None = None


class RefreshTokenStore(Protocol):
    def save(self, record: RefreshTokenRecord) -> None:
        """Persist a new refresh token record."""

    def get(self, token_hash: str) -> RefreshTokenRecord | None:
        """Load refresh token metadata by SHA256 hash."""

    def revoke(
        self,
        token_hash: str,
        *,
        revoked_at: int,
        replaced_by_jti: str | None = None,
    ) -> None:
        """Mark refresh token as no longer usable."""


@dataclass(slots=True)
class InMemoryRefreshTokenStore:
    """In-memory refresh store for tests and local service wiring."""

    records: dict[str, RefreshTokenRecord] = field(default_factory=dict)

    def save(self, record: RefreshTokenRecord) -> None:
        self.records[record.token_hash] = record

    def get(self, token_hash: str) -> RefreshTokenRecord | None:
        return self.records.get(token_hash)

    def revoke(
        self,
        token_hash: str,
        *,
        revoked_at: int,
        replaced_by_jti: str | None = None,
    ) -> None:
        record = self.records.get(token_hash)
        if record is None:
            return

        self.records[token_hash] = replace(
            record,
            revoked_at=revoked_at,
            replaced_by_jti=replaced_by_jti,
        )


@dataclass(frozen=True, slots=True)
class TwoFactorConfirmation:
    """Successful 2FA confirmation for a sensitive operation."""

    tenant_id: str
    subject: str | None
    operation: str
    resource_id: str
    method: str
    confirmed_at: int
    correlation_id: str | None


class AuthTokenService:
    """JWT HS256 access-token issuer plus opaque refresh-token rotation."""

    def __init__(
        self,
        *,
        jwt_secret: str | bytes,
        refresh_store: RefreshTokenStore,
        issuer: str,
        audience: str,
        access_ttl_seconds: int,
        refresh_ttl_seconds: int,
        clock: Clock | None = None,
        id_factory: TokenFactory | None = None,
        refresh_token_factory: TokenFactory | None = None,
    ) -> None:
        if access_ttl_seconds <= 0:
            raise ValueError("access_ttl_seconds должен быть положительным")
        if refresh_ttl_seconds <= 0:
            raise ValueError("refresh_ttl_seconds должен быть положительным")

        self._jwt_secret = jwt_secret
        self._refresh_store = refresh_store
        self._issuer = issuer
        self._audience = audience
        self._access_ttl_seconds = access_ttl_seconds
        self._refresh_ttl_seconds = refresh_ttl_seconds
        self._clock = clock or time.time
        self._id_factory = id_factory or _random_token_id
        self._refresh_token_factory = refresh_token_factory or _random_refresh_token

    def issue_token_pair(
        self,
        *,
        subject: str,
        tenant_id: str,
        roles: Sequence[str] = (),
        correlation_id: str | None = None,
    ) -> TokenPair:
        issued_at = int(self._clock())
        roles_tuple = _normalize_roles(roles)
        _require_non_empty(subject, "subject")
        tenant_context_from_claims(
            {
                "tenant_id": tenant_id,
                "sub": subject,
                "roles": list(roles_tuple),
            },
            correlation_id=correlation_id,
        )

        access_token = self._issue_access_token(
            subject=subject,
            tenant_id=tenant_id,
            roles=roles_tuple,
            issued_at=issued_at,
        )
        refresh_token = self._refresh_token_factory()
        refresh_jti = self._id_factory()
        record = RefreshTokenRecord(
            token_hash=hash_refresh_token(refresh_token),
            tenant_id=tenant_id,
            subject=subject,
            roles=roles_tuple,
            refresh_jti=refresh_jti,
            issued_at=issued_at,
            expires_at=issued_at + self._refresh_ttl_seconds,
        )
        self._refresh_store.save(record)

        return TokenPair(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="Bearer",
            expires_in=self._access_ttl_seconds,
            refresh_expires_in=self._refresh_ttl_seconds,
        )

    def verify_access_token(
        self,
        token: str,
        *,
        correlation_id: str | None = None,
    ) -> TenantContext:
        claims = decode_hs256_jwt(
            token,
            self._jwt_secret,
            expected_issuer=self._issuer,
            expected_audience=self._audience,
            now=self._clock(),
        )
        if claims.get("typ") != "access":
            raise UnauthorizedError(
                "JWT должен быть access token",
                correlation_id=correlation_id,
            )
        if not isinstance(claims.get("jti"), str):
            raise UnauthorizedError(
                "JWT claim jti должен быть строкой",
                correlation_id=correlation_id,
            )

        return tenant_context_from_claims(claims, correlation_id=correlation_id)

    def refresh_token_pair(
        self,
        refresh_token: str,
        *,
        correlation_id: str | None = None,
    ) -> TokenPair:
        now = int(self._clock())
        token_hash = hash_refresh_token(refresh_token)
        record = self._refresh_store.get(token_hash)

        if record is None or record.revoked_at is not None:
            raise UnauthorizedError(
                "Refresh token недействителен или уже использован",
                correlation_id=correlation_id,
            )
        if now >= record.expires_at:
            self._refresh_store.revoke(token_hash, revoked_at=now)
            raise UnauthorizedError(
                "Refresh token истёк",
                correlation_id=correlation_id,
            )

        token_pair = self.issue_token_pair(
            subject=record.subject,
            tenant_id=record.tenant_id,
            roles=record.roles,
            correlation_id=correlation_id,
        )
        replacement = self._refresh_store.get(
            hash_refresh_token(token_pair.refresh_token),
        )
        self._refresh_store.revoke(
            token_hash,
            revoked_at=now,
            replaced_by_jti=(
                replacement.refresh_jti if replacement is not None else None
            ),
        )

        return token_pair

    def revoke_refresh_token(
        self,
        refresh_token: str,
    ) -> None:
        self._refresh_store.revoke(
            hash_refresh_token(refresh_token),
            revoked_at=int(self._clock()),
        )

    def _issue_access_token(
        self,
        *,
        subject: str,
        tenant_id: str,
        roles: tuple[str, ...],
        issued_at: int,
    ) -> str:
        return encode_hs256_jwt(
            {
                "aud": self._audience,
                "exp": issued_at + self._access_ttl_seconds,
                "iat": issued_at,
                "iss": self._issuer,
                "jti": self._id_factory(),
                "nbf": issued_at,
                "roles": list(roles),
                "sub": subject,
                "tenant_id": tenant_id,
                "typ": "access",
            },
            self._jwt_secret,
        )


class TOTPService:
    """RFC 6238 TOTP helper for payout and other sensitive confirmations."""

    def __init__(
        self,
        *,
        issuer: str = "NMC",
        step_seconds: int = 30,
        digits: int = 6,
        allowed_drift_steps: int = 1,
        clock: Clock | None = None,
    ) -> None:
        if step_seconds <= 0:
            raise ValueError("step_seconds должен быть положительным")
        if digits < 6 or digits > 8:
            raise ValueError("digits должен быть в диапазоне 6..8")
        if allowed_drift_steps < 0:
            raise ValueError("allowed_drift_steps не может быть отрицательным")

        self._issuer = issuer
        self._step_seconds = step_seconds
        self._digits = digits
        self._allowed_drift_steps = allowed_drift_steps
        self._clock = clock or time.time

    def generate_secret(self, *, bytes_count: int = 20) -> str:
        if bytes_count < 16:
            raise ValueError("bytes_count должен быть не меньше 16")

        return (
            base64.b32encode(secrets.token_bytes(bytes_count))
            .decode(
                "ascii",
            )
            .rstrip("=")
        )

    def provisioning_uri(
        self,
        *,
        secret: str,
        account_name: str,
    ) -> str:
        _decode_totp_secret(secret)
        label = f"{self._issuer}:{account_name}"
        query = urlencode(
            {
                "secret": _normalize_base32_secret(secret),
                "issuer": self._issuer,
                "algorithm": "SHA1",
                "digits": str(self._digits),
                "period": str(self._step_seconds),
            }
        )

        return f"otpauth://totp/{quote(label)}?{query}"

    def generate_totp(
        self,
        secret: str,
        *,
        at_time: float | None = None,
    ) -> str:
        counter = self._counter(at_time)
        return _hotp(secret, counter, digits=self._digits)

    def verify_totp(
        self,
        secret: str,
        code: str,
        *,
        at_time: float | None = None,
    ) -> bool:
        normalized_code = code.strip().replace(" ", "")
        if len(normalized_code) != self._digits or not normalized_code.isdigit():
            return False

        counter = self._counter(at_time)
        for drift in range(-self._allowed_drift_steps, self._allowed_drift_steps + 1):
            candidate_counter = counter + drift
            if candidate_counter < 0:
                continue
            expected = _hotp(secret, candidate_counter, digits=self._digits)
            if hmac.compare_digest(expected, normalized_code):
                return True

        return False

    def confirm_sensitive_operation(
        self,
        *,
        context: TenantContext,
        secret: str,
        code: str,
        operation: str,
        resource_id: str,
        at_time: float | None = None,
    ) -> TwoFactorConfirmation:
        confirmed_at = int(self._clock() if at_time is None else at_time)
        if not self.verify_totp(secret, code, at_time=confirmed_at):
            raise UnauthorizedError(
                "2FA code недействителен",
                correlation_id=context.correlation_id,
            )

        return TwoFactorConfirmation(
            tenant_id=context.tenant_id,
            subject=context.subject,
            operation=operation,
            resource_id=resource_id,
            method="totp",
            confirmed_at=confirmed_at,
            correlation_id=context.correlation_id,
        )

    def _counter(self, at_time: float | None) -> int:
        timestamp = self._clock() if at_time is None else at_time
        return int(timestamp // self._step_seconds)


def hash_refresh_token(refresh_token: str) -> str:
    return hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()


def _normalize_roles(roles: Sequence[str]) -> tuple[str, ...]:
    normalized = tuple(roles)
    if any(not isinstance(role, str) or role.strip() == "" for role in normalized):
        raise ValueError("roles должен содержать только непустые строки")

    return normalized


def _require_non_empty(value: str, field_name: str) -> None:
    if value.strip() == "":
        raise ValueError(f"{field_name} должен быть непустой строкой")


def _random_token_id() -> str:
    return secrets.token_urlsafe(16)


def _random_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def _hotp(secret: str, counter: int, *, digits: int) -> str:
    key = _decode_totp_secret(secret)
    counter_bytes = counter.to_bytes(8, "big")
    digest = hmac.new(key, counter_bytes, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF

    return str(code % (10**digits)).zfill(digits)


def _decode_totp_secret(secret: str) -> bytes:
    normalized = _normalize_base32_secret(secret)
    padding = "=" * (-len(normalized) % 8)
    try:
        return base64.b32decode(f"{normalized}{padding}", casefold=True)
    except (binascii.Error, ValueError) as error:
        raise UnauthorizedError("2FA secret имеет недопустимый base32") from error


def _normalize_base32_secret(secret: str) -> str:
    return "".join(secret.upper().split())
