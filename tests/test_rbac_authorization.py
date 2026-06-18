from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import cast

from libs.shared import (
    BLOCKCHAIN_AUDIT_ENDPOINT_POLICIES,
    GOVERNANCE_ROLES,
    AccessPolicy,
    ForbiddenError,
    RBACASGIMiddleware,
    TenantContext,
    TenantContextASGIMiddleware,
    encode_hs256_jwt,
    require_access,
)

NOW = 1_800_000_000
SECRET = "local-dev-secret"


async def _empty_receive() -> dict[str, object]:
    return {"type": "http.request", "body": b"", "more_body": False}


def _sender(
    messages: list[dict[str, object]],
) -> Callable[[dict[str, object]], Awaitable[None]]:
    async def send(message: dict[str, object]) -> None:
        messages.append(message)

    return send


def _http_scope(
    *,
    method: str,
    path: str,
    headers: list[tuple[bytes, bytes]],
) -> dict[str, object]:
    return {
        "type": "http",
        "method": method,
        "path": path,
        "headers": headers,
    }


def _jwt_for_role(role: str) -> str:
    return encode_hs256_jwt(
        {
            "tenant_id": "tenant-a",
            "sub": f"{role}-1",
            "roles": [role],
            "iss": "nmc",
            "aud": "api-gateway",
            "exp": NOW + 60,
        },
        SECRET,
    )


def _capture_forbidden(callback: Callable[[], object]) -> ForbiddenError:
    try:
        callback()
    except ForbiddenError as error:
        return error

    raise AssertionError("Ожидался ForbiddenError")


def _require_blockchain_audit_policy_for_role(
    policy: AccessPolicy,
    role: str,
) -> TenantContext:
    return require_access(
        policy,
        context=TenantContext(
            tenant_id="tenant-a",
            subject=f"{role}-1",
            roles=(role,),
            correlation_id="corr-rbac-2",
        ),
    )


def _forbidden_role_callback(
    policy: AccessPolicy,
    role: str,
) -> Callable[[], object]:
    def callback() -> object:
        return _require_blockchain_audit_policy_for_role(policy, role)

    return callback


def test_governance_roles_are_fixed_for_stage_one_rbac() -> None:
    assert GOVERNANCE_ROLES == (
        "council",
        "presidium",
        "board",
        "member_full",
        "member_assoc",
        "audience",
    )


def test_blockchain_audit_policy_allows_only_council_role() -> None:
    policy = AccessPolicy.allow_roles(
        "council",
        action="blockchain_audit.verify",
        resource_type="blockchain_auditor",
    )

    council_context = TenantContext(
        tenant_id="tenant-a",
        subject="council-1",
        roles=("council",),
        correlation_id="corr-rbac-1",
    )
    assert require_access(policy, context=council_context) == council_context

    for denied_role in GOVERNANCE_ROLES:
        if denied_role == "council":
            continue

        error = _capture_forbidden(_forbidden_role_callback(policy, denied_role))

        assert error.status_code == 403
        assert error.error_code == "forbidden"
        assert error.correlation_id == "corr-rbac-2"
        assert error.details == {
            "action": "blockchain_audit.verify",
            "resource_type": "blockchain_auditor",
            "required_roles": ["council"],
        }


def test_rbac_asgi_middleware_enforces_endpoint_policy_after_tenant_auth() -> None:
    asyncio.run(_run_rbac_asgi_middleware_scenario())


async def _run_rbac_asgi_middleware_scenario() -> None:
    async def app(
        _scope: dict[str, object],
        _receive: Callable[[], Awaitable[dict[str, object]]],
        send: Callable[[dict[str, object]], Awaitable[None]],
    ) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": b'{"ok": true}'})

    rbac_app = RBACASGIMiddleware(
        app,
        endpoint_policies=BLOCKCHAIN_AUDIT_ENDPOINT_POLICIES,
    )
    middleware = TenantContextASGIMiddleware(
        rbac_app,
        jwt_secret=SECRET,
        expected_issuer="nmc",
        expected_audience="api-gateway",
        clock=lambda: NOW,
    )

    allowed_messages: list[dict[str, object]] = []
    await middleware(
        _http_scope(
            method="POST",
            path="/audit/record",
            headers=[
                (b"authorization", f"Bearer {_jwt_for_role('council')}".encode()),
                (b"x-correlation-id", b"corr-rbac-3"),
            ],
        ),
        _empty_receive,
        _sender(allowed_messages),
    )

    assert allowed_messages[0]["status"] == 200

    denied_messages: list[dict[str, object]] = []
    await middleware(
        _http_scope(
            method="POST",
            path="/audit/record",
            headers=[
                (b"authorization", f"Bearer {_jwt_for_role('presidium')}".encode()),
                (b"x-correlation-id", b"corr-rbac-4"),
            ],
        ),
        _empty_receive,
        _sender(denied_messages),
    )

    assert denied_messages[0]["status"] == 403
    response_body = json.loads(cast(bytes, denied_messages[1]["body"]))
    assert response_body["error"] == {
        "code": "forbidden",
        "message": "Недостаточно роли для операции",
        "details": {
            "action": "blockchain_audit.record",
            "resource_type": "blockchain_auditor",
            "required_roles": ["council"],
        },
        "correlation_id": "corr-rbac-4",
    }
