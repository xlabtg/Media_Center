from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import cast

from libs.shared import (
    APIGatewayASGIMiddleware,
    GatewayRoute,
    InMemoryRateLimiter,
    RateLimitPolicy,
    TenantContextASGIMiddleware,
    encode_hs256_jwt,
)

NOW = 1_800_000_000
SECRET = "local-dev-secret"

ASGIMessage = dict[str, object]
ASGIScope = dict[str, object]
Receive = Callable[[], Awaitable[ASGIMessage]]
Send = Callable[[ASGIMessage], Awaitable[None]]
ASGIApp = Callable[[ASGIScope, Receive, Send], Awaitable[None]]


async def _empty_receive() -> ASGIMessage:
    return {"type": "http.request", "body": b"", "more_body": False}


def _sender(
    messages: list[ASGIMessage],
) -> Send:
    async def send(message: ASGIMessage) -> None:
        messages.append(message)

    return send


def _http_scope(
    *,
    path: str,
    headers: list[tuple[bytes, bytes]],
    method: str = "GET",
) -> ASGIScope:
    return {
        "type": "http",
        "method": method,
        "path": path,
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
            "typ": "access",
            "jti": f"jti-{tenant_id}",
        },
        SECRET,
    )


def _gateway_app(
    downstream: ASGIApp,
    *,
    limit: int = 10,
) -> TenantContextASGIMiddleware:
    return TenantContextASGIMiddleware(
        APIGatewayASGIMiddleware(
            routes=(
                GatewayRoute(
                    service_name="contribution-ledger",
                    path_prefix="/contribution-ledger",
                    downstream_app=downstream,
                ),
            ),
            rate_limiter=InMemoryRateLimiter(
                RateLimitPolicy(limit=limit, window_seconds=60),
                clock=lambda: NOW,
            ),
        ),
        jwt_secret=SECRET,
        expected_issuer="nmc",
        expected_audience="api-gateway",
        clock=lambda: NOW,
    )


async def _call(
    app: ASGIApp,
    *,
    tenant_id: str = "tenant-a",
    path: str = "/contribution-ledger/contributions",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> list[ASGIMessage]:
    request_headers = [
        (b"authorization", f"Bearer {_jwt_for_tenant(tenant_id)}".encode()),
        (b"x-correlation-id", b"corr-gw-1"),
    ]
    if headers is not None:
        request_headers.extend(headers)

    messages: list[ASGIMessage] = []
    await app(
        _http_scope(path=path, headers=request_headers),
        _empty_receive,
        _sender(messages),
    )
    return messages


@dataclass(slots=True)
class CapturingDownstream:
    calls: list[dict[str, object]] = field(default_factory=list)

    async def __call__(
        self,
        scope: ASGIScope,
        _receive: Receive,
        send: Send,
    ) -> None:
        self.calls.append(scope)
        headers = {
            name.decode(): value.decode()
            for name, value in cast(list[tuple[bytes, bytes]], scope["headers"])
        }
        body = json.dumps(
            {
                "path": scope["path"],
                "tenant_id": headers["x-tenant-id"],
                "subject_id": headers["x-subject-id"],
                "roles": headers["x-actor-roles"],
                "correlation_id": headers["x-correlation-id"],
                "service_name": headers["x-service-name"],
            }
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body})


def test_gateway_routes_by_service_prefix_and_forwards_tenant_context() -> None:
    asyncio.run(_run_gateway_routing_scenario())


async def _run_gateway_routing_scenario() -> None:
    downstream = CapturingDownstream()
    messages = await _call(_gateway_app(downstream))

    assert messages[0]["status"] == 200
    response_body = json.loads(cast(bytes, messages[1]["body"]))
    assert response_body == {
        "path": "/contributions",
        "tenant_id": "tenant-a",
        "subject_id": "member-1",
        "roles": "member_full",
        "correlation_id": "corr-gw-1",
        "service_name": "api-gateway",
    }
    assert len(downstream.calls) == 1
    forwarded_headers = cast(
        list[tuple[bytes, bytes]],
        downstream.calls[0]["headers"],
    )
    assert _header_count(forwarded_headers, b"x-correlation-id") == 1
    assert _header_count(forwarded_headers, b"x-tenant-id") == 1


def test_gateway_rejects_tenant_override_before_downstream_call() -> None:
    asyncio.run(_run_gateway_tenant_override_scenario())


async def _run_gateway_tenant_override_scenario() -> None:
    downstream = CapturingDownstream()
    messages = await _call(
        _gateway_app(downstream),
        headers=[(b"x-tenant-id", b"tenant-b")],
    )

    assert messages[0]["status"] == 403
    response_body = json.loads(cast(bytes, messages[1]["body"]))
    assert response_body["error"]["code"] == "tenant_isolation_violation"
    assert downstream.calls == []


def test_gateway_rate_limits_per_tenant_and_service() -> None:
    asyncio.run(_run_gateway_rate_limit_scenario())


async def _run_gateway_rate_limit_scenario() -> None:
    downstream = CapturingDownstream()
    app = _gateway_app(downstream, limit=2)

    first_messages = await _call(app)
    second_messages = await _call(app)
    third_messages = await _call(app)
    other_tenant_messages = await _call(app, tenant_id="tenant-b")

    assert first_messages[0]["status"] == 200
    assert second_messages[0]["status"] == 200
    assert third_messages[0]["status"] == 429
    response_body = json.loads(cast(bytes, third_messages[1]["body"]))
    assert response_body["error"]["code"] == "rate_limited"
    assert other_tenant_messages[0]["status"] == 200
    assert len(downstream.calls) == 3


def _header_count(headers: list[tuple[bytes, bytes]], name: bytes) -> int:
    return sum(1 for header_name, _value in headers if header_name.lower() == name)
