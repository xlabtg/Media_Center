from __future__ import annotations

import base64
import binascii
import contextvars
import hashlib
import hmac
import json
import re
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import NoReturn, Protocol, cast

TENANT_ISOLATION_CODE = "tenant_isolation_violation"
UNAUTHORIZED_CODE = "unauthorized"
TENANT_ISOLATION_EVENT = "tenant.isolation_violation"
TENANT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")

ASGIMessage = dict[str, object]
ASGIScope = dict[str, object]
Receive = Callable[[], Awaitable[ASGIMessage]]
Send = Callable[[ASGIMessage], Awaitable[None]]
ASGIApp = Callable[[ASGIScope, Receive, Send], Awaitable[None]]
Clock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Verified tenant identity attached to the current request."""

    tenant_id: str
    subject: str | None = None
    roles: tuple[str, ...] = ()
    correlation_id: str | None = None


@dataclass(frozen=True, slots=True)
class TenantAuditEvent:
    """Sanitized audit event for tenant-isolation denials."""

    event_type: str
    tenant_id: str | None
    requested_tenant_hash: str | None
    actor_hash: str | None
    resource_type: str
    correlation_id: str | None
    reason: str
    status_code: int = 403
    error_code: str = TENANT_ISOLATION_CODE

    def as_dict(self) -> dict[str, object]:
        return {
            "event_type": self.event_type,
            "tenant_id": self.tenant_id,
            "requested_tenant_hash": self.requested_tenant_hash,
            "actor_hash": self.actor_hash,
            "resource_type": self.resource_type,
            "correlation_id": self.correlation_id,
            "reason": self.reason,
            "status_code": self.status_code,
            "error_code": self.error_code,
        }


class AuditSink(Protocol):
    def record(self, event: TenantAuditEvent) -> None:
        """Persist or publish a sanitized audit event."""


@dataclass(slots=True)
class InMemoryAuditSink:
    """Small audit sink for unit tests and local service wiring."""

    events: list[TenantAuditEvent] = field(default_factory=list)

    def record(self, event: TenantAuditEvent) -> None:
        self.events.append(event)


class TenantCoreError(Exception):
    """Base error that renders into the project-wide error envelope."""

    def __init__(
        self,
        *,
        status_code: int,
        error_code: str,
        message: str,
        details: Mapping[str, object] | None = None,
        correlation_id: str | None = None,
        audited: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.message = message
        self.details = dict(details or {})
        self.correlation_id = correlation_id
        self.audited = audited

    def to_response_body(self) -> dict[str, object]:
        return {
            "error": {
                "code": self.error_code,
                "message": self.message,
                "details": self.details,
                "correlation_id": self.correlation_id,
            }
        }


class TenantIsolationError(TenantCoreError):
    def __init__(
        self,
        message: str = "Доступ к ресурсу другого tenant запрещён",
        *,
        details: Mapping[str, object] | None = None,
        correlation_id: str | None = None,
        audited: bool = False,
    ) -> None:
        super().__init__(
            status_code=403,
            error_code=TENANT_ISOLATION_CODE,
            message=message,
            details=details,
            correlation_id=correlation_id,
            audited=audited,
        )


class UnauthorizedError(TenantCoreError):
    def __init__(
        self,
        message: str = "Нет валидного JWT или service credentials",
        *,
        details: Mapping[str, object] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        super().__init__(
            status_code=401,
            error_code=UNAUTHORIZED_CODE,
            message=message,
            details=details,
            correlation_id=correlation_id,
        )


_current_tenant_context: contextvars.ContextVar[TenantContext | None] = (
    contextvars.ContextVar("tenant_context", default=None)
)


def set_tenant_context(
    context: TenantContext,
) -> contextvars.Token[TenantContext | None]:
    return _current_tenant_context.set(context)


def reset_tenant_context(token: contextvars.Token[TenantContext | None]) -> None:
    _current_tenant_context.reset(token)


def get_tenant_context() -> TenantContext | None:
    return _current_tenant_context.get()


def require_tenant_context() -> TenantContext:
    context = get_tenant_context()
    if context is None:
        raise TenantIsolationError("Tenant context отсутствует")
    return context


def encode_hs256_jwt(
    claims: Mapping[str, object],
    secret: str | bytes,
    *,
    headers: Mapping[str, object] | None = None,
) -> str:
    jwt_header: dict[str, object] = {"alg": "HS256", "typ": "JWT"}
    if headers is not None:
        jwt_header.update(headers)

    encoded_header = _base64url_encode(_json_bytes(jwt_header))
    encoded_claims = _base64url_encode(_json_bytes(claims))
    signing_input = f"{encoded_header}.{encoded_claims}"
    signature = _sign_hs256(signing_input, secret)

    return f"{signing_input}.{_base64url_encode(signature)}"


def decode_hs256_jwt(
    token: str,
    secret: str | bytes,
    *,
    expected_issuer: str | None = None,
    expected_audience: str | None = None,
    now: float | None = None,
) -> dict[str, object]:
    parts = token.split(".")
    if len(parts) != 3:
        raise UnauthorizedError("JWT должен состоять из header, payload и signature")

    header_segment, payload_segment, signature_segment = parts
    header = _decode_json_segment(header_segment)
    claims = _decode_json_segment(payload_segment)

    if header.get("alg") != "HS256":
        raise UnauthorizedError("JWT должен использовать алгоритм HS256")

    signing_input = f"{header_segment}.{payload_segment}"
    expected_signature = _sign_hs256(signing_input, secret)
    try:
        actual_signature = _base64url_decode(signature_segment)
    except binascii.Error as error:
        raise UnauthorizedError("JWT signature имеет недопустимый base64url") from error

    if not hmac.compare_digest(expected_signature, actual_signature):
        raise UnauthorizedError("Подпись JWT недействительна")

    _validate_registered_claims(
        claims,
        expected_issuer=expected_issuer,
        expected_audience=expected_audience,
        now=now,
    )
    return claims


def tenant_context_from_authorization_header(
    authorization_header: str | None,
    secret: str | bytes,
    *,
    expected_issuer: str | None = None,
    expected_audience: str | None = None,
    correlation_id: str | None = None,
    now: float | None = None,
) -> TenantContext:
    token = _extract_bearer_token(authorization_header, correlation_id)
    claims = decode_hs256_jwt(
        token,
        secret,
        expected_issuer=expected_issuer,
        expected_audience=expected_audience,
        now=now,
    )
    return tenant_context_from_claims(claims, correlation_id=correlation_id)


def tenant_context_from_claims(
    claims: Mapping[str, object],
    *,
    correlation_id: str | None = None,
) -> TenantContext:
    tenant_id = _validated_tenant_id(
        claims.get("tenant_id"),
        correlation_id=correlation_id,
    )
    subject = _optional_string_claim(claims, "sub")
    roles = _roles_from_claims(claims)

    return TenantContext(
        tenant_id=tenant_id,
        subject=subject,
        roles=roles,
        correlation_id=correlation_id,
    )


def assert_requested_tenant(
    requested_tenant_id: str | None,
    *,
    context: TenantContext | None = None,
    audit_sink: AuditSink | None = None,
    resource_type: str = "request",
) -> None:
    resolved_context = _resolve_context(context)
    if requested_tenant_id is None:
        return

    normalized_tenant = _validated_tenant_id(
        requested_tenant_id,
        correlation_id=resolved_context.correlation_id,
    )
    if normalized_tenant != resolved_context.tenant_id:
        _deny_tenant_access(
            context=resolved_context,
            requested_tenant_id=normalized_tenant,
            resource_type=resource_type,
            audit_sink=audit_sink,
            reason="requested_tenant_mismatch",
        )


class TenantContextASGIMiddleware:
    """ASGI middleware compatible with FastAPI/Starlette applications."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        jwt_secret: str | bytes,
        expected_issuer: str | None = None,
        expected_audience: str | None = None,
        audit_sink: AuditSink | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._app = app
        self._jwt_secret = jwt_secret
        self._expected_issuer = expected_issuer
        self._expected_audience = expected_audience
        self._audit_sink = audit_sink
        self._clock = clock or time.time

    async def __call__(
        self,
        scope: ASGIScope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        headers = _headers_from_scope(scope)
        correlation_id = headers.get("x-correlation-id")

        try:
            context = tenant_context_from_authorization_header(
                headers.get("authorization"),
                self._jwt_secret,
                expected_issuer=self._expected_issuer,
                expected_audience=self._expected_audience,
                correlation_id=correlation_id,
                now=self._clock(),
            )
            assert_requested_tenant(
                headers.get("x-tenant-id"),
                context=context,
                audit_sink=self._audit_sink,
                resource_type="http_header",
            )
        except TenantIsolationError as error:
            self._audit_missing_denial(error, correlation_id)
            await _send_error(send, error)
            return
        except UnauthorizedError as error:
            await _send_error(send, error)
            return

        token = set_tenant_context(context)
        try:
            await self._app(scope, receive, send)
        finally:
            reset_tenant_context(token)

    def _audit_missing_denial(
        self,
        error: TenantIsolationError,
        correlation_id: str | None,
    ) -> None:
        if error.audited or self._audit_sink is None:
            return

        self._audit_sink.record(
            _build_tenant_violation_event(
                context=None,
                requested_tenant_id=None,
                resource_type="http_request",
                correlation_id=error.correlation_id or correlation_id,
                reason=error.message,
            )
        )
        error.audited = True


class TenantScopedRepository[RecordT]:
    """Base helper that makes tenant filtering explicit and repeatable."""

    def __init__(self, resource_type: str) -> None:
        self.resource_type = resource_type

    def tenant_filter(self, context: TenantContext | None = None) -> dict[str, str]:
        resolved_context = _resolve_context(context)
        return {"tenant_id": resolved_context.tenant_id}

    def list_for_tenant(
        self,
        records: Iterable[RecordT],
        context: TenantContext | None = None,
    ) -> list[RecordT]:
        resolved_context = _resolve_context(context)
        return [
            record
            for record in records
            if _record_tenant_id(
                record,
                resource_type=self.resource_type,
                correlation_id=resolved_context.correlation_id,
            )
            == resolved_context.tenant_id
        ]

    def get_owned(
        self,
        records: Iterable[RecordT],
        resource_id: object,
        context: TenantContext | None = None,
        *,
        id_field: str = "id",
        audit_sink: AuditSink | None = None,
    ) -> RecordT | None:
        resolved_context = _resolve_context(context)
        for record in records:
            if _record_field(record, id_field) == resource_id:
                return self.require_owned(
                    record,
                    resolved_context,
                    audit_sink=audit_sink,
                )

        return None

    def require_owned(
        self,
        record: RecordT,
        context: TenantContext | None = None,
        *,
        audit_sink: AuditSink | None = None,
    ) -> RecordT:
        resolved_context = _resolve_context(context)
        record_tenant_id = _record_tenant_id(
            record,
            resource_type=self.resource_type,
            correlation_id=resolved_context.correlation_id,
        )

        if record_tenant_id != resolved_context.tenant_id:
            _deny_tenant_access(
                context=resolved_context,
                requested_tenant_id=record_tenant_id,
                resource_type=self.resource_type,
                audit_sink=audit_sink,
                reason="resource_tenant_mismatch",
            )

        return record


def _deny_tenant_access(
    *,
    context: TenantContext,
    requested_tenant_id: str | None,
    resource_type: str,
    audit_sink: AuditSink | None,
    reason: str,
) -> NoReturn:
    if audit_sink is not None:
        audit_sink.record(
            _build_tenant_violation_event(
                context=context,
                requested_tenant_id=requested_tenant_id,
                resource_type=resource_type,
                correlation_id=context.correlation_id,
                reason=reason,
            )
        )

    raise TenantIsolationError(
        correlation_id=context.correlation_id,
        audited=audit_sink is not None,
    )


def _build_tenant_violation_event(
    *,
    context: TenantContext | None,
    requested_tenant_id: str | None,
    resource_type: str,
    correlation_id: str | None,
    reason: str,
) -> TenantAuditEvent:
    return TenantAuditEvent(
        event_type=TENANT_ISOLATION_EVENT,
        tenant_id=context.tenant_id if context is not None else None,
        requested_tenant_hash=_hash_identifier(requested_tenant_id),
        actor_hash=_hash_identifier(context.subject if context is not None else None),
        resource_type=resource_type,
        correlation_id=correlation_id,
        reason=reason,
    )


def _hash_identifier(value: str | None) -> str | None:
    if value is None:
        return None

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _resolve_context(context: TenantContext | None) -> TenantContext:
    if context is not None:
        return context

    return require_tenant_context()


def _validated_tenant_id(
    value: object,
    *,
    correlation_id: str | None,
) -> str:
    if not isinstance(value, str) or not TENANT_ID_PATTERN.fullmatch(value):
        raise TenantIsolationError(
            "Tenant context отсутствует или имеет недопустимый формат",
            correlation_id=correlation_id,
        )

    return value


def _optional_string_claim(claims: Mapping[str, object], key: str) -> str | None:
    value = claims.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise UnauthorizedError(f"JWT claim {key} должен быть строкой")

    return value


def _roles_from_claims(claims: Mapping[str, object]) -> tuple[str, ...]:
    roles = claims.get("roles")
    if roles is None:
        scope = claims.get("scope")
        if isinstance(scope, str):
            return tuple(scope.split())
        return ()

    if isinstance(roles, str):
        return (roles,)

    if isinstance(roles, Sequence) and not isinstance(roles, bytes | bytearray):
        normalized_roles = tuple(role for role in roles if isinstance(role, str))
        if len(normalized_roles) == len(roles):
            return normalized_roles

    raise UnauthorizedError("JWT claim roles должен быть строкой или списком строк")


def _extract_bearer_token(
    authorization_header: str | None,
    correlation_id: str | None,
) -> str:
    if authorization_header is None:
        raise UnauthorizedError(correlation_id=correlation_id)

    scheme, separator, token = authorization_header.strip().partition(" ")
    if separator == "" or scheme.lower() != "bearer" or token.strip() == "":
        raise UnauthorizedError("Authorization header должен иметь формат Bearer JWT")

    return token.strip()


def _validate_registered_claims(
    claims: Mapping[str, object],
    *,
    expected_issuer: str | None,
    expected_audience: str | None,
    now: float | None,
) -> None:
    timestamp = time.time() if now is None else now
    expires_at = claims.get("exp")
    if expires_at is not None and not _is_numeric_claim(expires_at):
        raise UnauthorizedError("JWT claim exp должен быть числом")
    if isinstance(expires_at, int | float) and timestamp >= float(expires_at):
        raise UnauthorizedError("JWT истёк")

    not_before = claims.get("nbf")
    if not_before is not None and not _is_numeric_claim(not_before):
        raise UnauthorizedError("JWT claim nbf должен быть числом")
    if isinstance(not_before, int | float) and timestamp < float(not_before):
        raise UnauthorizedError("JWT ещё не действует")

    if expected_issuer is not None and claims.get("iss") != expected_issuer:
        raise UnauthorizedError("JWT issuer не совпадает с ожидаемым")

    if expected_audience is not None and not _audience_matches(
        claims.get("aud"),
        expected_audience,
    ):
        raise UnauthorizedError("JWT audience не совпадает с ожидаемым")


def _is_numeric_claim(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _audience_matches(value: object, expected_audience: str) -> bool:
    if isinstance(value, str):
        return value == expected_audience

    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | str):
        return expected_audience in value

    return False


def _decode_json_segment(segment: str) -> dict[str, object]:
    try:
        decoded = json.loads(_base64url_decode(segment))
    except (binascii.Error, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise UnauthorizedError("JWT содержит некорректный JSON") from error

    if not isinstance(decoded, dict):
        raise UnauthorizedError("JWT segment должен быть JSON object")

    return cast(dict[str, object], decoded)


def _json_bytes(payload: Mapping[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sign_hs256(signing_input: str, secret: str | bytes) -> bytes:
    key = secret.encode("utf-8") if isinstance(secret, str) else secret
    return hmac.new(key, signing_input.encode("ascii"), hashlib.sha256).digest()


def _base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _base64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(f"{segment}{padding}")


async def _send_error(send: Send, error: TenantCoreError) -> None:
    body = json.dumps(error.to_response_body(), ensure_ascii=False).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": error.status_code,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _headers_from_scope(scope: ASGIScope) -> dict[str, str]:
    raw_headers = scope.get("headers", [])
    if not isinstance(raw_headers, list | tuple):
        return {}

    headers: dict[str, str] = {}
    for raw_name, raw_value in raw_headers:
        if not isinstance(raw_name, bytes) or not isinstance(raw_value, bytes):
            continue
        headers[raw_name.decode("latin-1").lower()] = raw_value.decode("latin-1")

    return headers


def _record_tenant_id(
    record: object,
    *,
    resource_type: str,
    correlation_id: str | None,
) -> str:
    return _validated_tenant_id(
        _record_field(record, "tenant_id"),
        correlation_id=correlation_id,
    )


def _record_field(record: object, field_name: str) -> object:
    if isinstance(record, Mapping):
        return record.get(field_name)

    return getattr(record, field_name, None)
