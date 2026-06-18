from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import cast

from libs.shared import (
    InMemoryAuditSink,
    TenantContext,
    TenantContextASGIMiddleware,
    TenantIsolationError,
    TenantScopedRepository,
    UnauthorizedError,
    encode_hs256_jwt,
    get_tenant_context,
    reset_tenant_context,
    set_tenant_context,
    tenant_context_from_authorization_header,
)

NOW = 1_800_000_000
SECRET = "local-dev-secret"


@dataclass(frozen=True)
class ContributionRecord:
    id: str
    tenant_id: str
    points: int


async def _empty_receive() -> dict[str, object]:
    return {"type": "http.request", "body": b"", "more_body": False}


def _sender(
    messages: list[dict[str, object]],
) -> Callable[[dict[str, object]], Awaitable[None]]:
    async def send(message: dict[str, object]) -> None:
        messages.append(message)

    return send


def _http_scope(headers: list[tuple[bytes, bytes]]) -> dict[str, object]:
    return {
        "type": "http",
        "method": "GET",
        "path": "/contributions",
        "headers": headers,
    }


def _jwt_for_tenant(tenant_id: str) -> str:
    return encode_hs256_jwt(
        {
            "tenant_id": tenant_id,
            "sub": "member-1",
            "roles": ["member_full"],
            "iss": "nmc",
            "aud": "api-gateway",
            "exp": NOW + 60,
        },
        SECRET,
    )


def _capture_tenant_isolation(
    callback: Callable[[], object],
) -> TenantIsolationError:
    try:
        callback()
    except TenantIsolationError as error:
        return error

    raise AssertionError("Ожидался TenantIsolationError")


def _capture_unauthorized(callback: Callable[[], object]) -> UnauthorizedError:
    try:
        callback()
    except UnauthorizedError as error:
        return error

    raise AssertionError("Ожидался UnauthorizedError")


def test_jwt_claims_create_validated_tenant_context() -> None:
    token = _jwt_for_tenant("tenant-a")

    context = tenant_context_from_authorization_header(
        f"Bearer {token}",
        SECRET,
        expected_issuer="nmc",
        expected_audience="api-gateway",
        correlation_id="corr-1",
        now=NOW,
    )

    assert context == TenantContext(
        tenant_id="tenant-a",
        subject="member-1",
        roles=("member_full",),
        correlation_id="corr-1",
    )


def test_jwt_without_tenant_claim_is_tenant_isolation_violation() -> None:
    token = encode_hs256_jwt(
        {
            "sub": "member-1",
            "roles": ["member_full"],
            "iss": "nmc",
            "aud": "api-gateway",
            "exp": NOW + 60,
        },
        SECRET,
    )

    error = _capture_tenant_isolation(
        lambda: tenant_context_from_authorization_header(
            f"Bearer {token}",
            SECRET,
            expected_issuer="nmc",
            expected_audience="api-gateway",
            correlation_id="corr-2",
            now=NOW,
        )
    )

    assert error.status_code == 403
    assert error.error_code == "tenant_isolation_violation"
    response_error = cast(
        dict[str, object],
        error.to_response_body()["error"],
    )
    assert response_error["correlation_id"] == "corr-2"


def test_invalid_jwt_returns_unauthorized_error() -> None:
    token = _jwt_for_tenant("tenant-a")
    tampered_token = f"{token[:-1]}x"

    error = _capture_unauthorized(
        lambda: tenant_context_from_authorization_header(
            f"Bearer {tampered_token}",
            SECRET,
            expected_issuer="nmc",
            expected_audience="api-gateway",
            now=NOW,
        )
    )

    assert error.status_code == 401
    assert error.error_code == "unauthorized"


def test_tenant_context_is_request_scoped() -> None:
    context = TenantContext(
        tenant_id="tenant-a",
        subject="member-1",
        roles=("member_full",),
        correlation_id="corr-3",
    )

    token = set_tenant_context(context)
    try:
        assert get_tenant_context() == context
    finally:
        reset_tenant_context(token)

    assert get_tenant_context() is None


def test_repository_forces_tenant_filter_and_audits_cross_tenant_denial() -> None:
    context = TenantContext(
        tenant_id="tenant-a",
        subject="member-1",
        roles=("member_full",),
        correlation_id="corr-4",
    )
    tenant_a_record = ContributionRecord("contrib-a", "tenant-a", 10)
    tenant_b_record = ContributionRecord("contrib-b", "tenant-b", 20)
    repository = TenantScopedRepository[ContributionRecord]("contributions")
    audit_sink = InMemoryAuditSink()

    assert repository.tenant_filter(context) == {"tenant_id": "tenant-a"}
    assert repository.list_for_tenant([tenant_a_record, tenant_b_record], context) == [
        tenant_a_record
    ]

    error = _capture_tenant_isolation(
        lambda: repository.require_owned(
            tenant_b_record,
            context,
            audit_sink=audit_sink,
        )
    )

    assert error.status_code == 403
    assert error.error_code == "tenant_isolation_violation"
    assert len(audit_sink.events) == 1
    assert audit_sink.events[0].event_type == "tenant.isolation_violation"
    assert audit_sink.events[0].tenant_id == "tenant-a"
    assert audit_sink.events[0].requested_tenant_hash is not None
    assert audit_sink.events[0].requested_tenant_hash != "tenant-b"
    assert audit_sink.events[0].correlation_id == "corr-4"


def test_asgi_middleware_sets_context_and_rejects_tenant_override() -> None:
    asyncio.run(_run_asgi_middleware_scenario())


async def _run_asgi_middleware_scenario() -> None:
    async def app(
        _scope: dict[str, object],
        _receive: Callable[[], Awaitable[dict[str, object]]],
        send: Callable[[dict[str, object]], Awaitable[None]],
    ) -> None:
        context = get_tenant_context()
        assert context is not None
        body = json.dumps({"tenant_id": context.tenant_id}).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body})

    audit_sink = InMemoryAuditSink()
    middleware = TenantContextASGIMiddleware(
        app,
        jwt_secret=SECRET,
        expected_issuer="nmc",
        expected_audience="api-gateway",
        audit_sink=audit_sink,
        clock=lambda: NOW,
    )

    success_messages: list[dict[str, object]] = []
    await middleware(
        _http_scope(
            [
                (b"authorization", f"Bearer {_jwt_for_tenant('tenant-a')}".encode()),
                (b"x-correlation-id", b"corr-5"),
            ]
        ),
        _empty_receive,
        _sender(success_messages),
    )

    assert success_messages[0]["status"] == 200
    assert json.loads(cast(bytes, success_messages[1]["body"])) == {
        "tenant_id": "tenant-a"
    }

    denial_messages: list[dict[str, object]] = []
    await middleware(
        _http_scope(
            [
                (b"authorization", f"Bearer {_jwt_for_tenant('tenant-a')}".encode()),
                (b"x-tenant-id", b"tenant-b"),
                (b"x-correlation-id", b"corr-6"),
            ]
        ),
        _empty_receive,
        _sender(denial_messages),
    )

    assert denial_messages[0]["status"] == 403
    response_body = json.loads(cast(bytes, denial_messages[1]["body"]))
    assert response_body["error"]["code"] == "tenant_isolation_violation"
    assert response_body["error"]["correlation_id"] == "corr-6"
    assert audit_sink.events[-1].event_type == "tenant.isolation_violation"
