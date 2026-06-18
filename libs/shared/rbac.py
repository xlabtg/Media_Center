from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

from libs.shared.tenant import (
    TenantContext,
    TenantCoreError,
    require_tenant_context,
)

FORBIDDEN_CODE = "forbidden"
COUNCIL_ROLE = "council"
PRESIDIUM_ROLE = "presidium"
BOARD_ROLE = "board"
MEMBER_FULL_ROLE = "member_full"
MEMBER_ASSOC_ROLE = "member_assoc"
AUDIENCE_ROLE = "audience"

GOVERNANCE_ROLES: tuple[str, ...] = (
    COUNCIL_ROLE,
    PRESIDIUM_ROLE,
    BOARD_ROLE,
    MEMBER_FULL_ROLE,
    MEMBER_ASSOC_ROLE,
    AUDIENCE_ROLE,
)
GOVERNANCE_ROLE_SET = frozenset(GOVERNANCE_ROLES)

ASGIMessage = dict[str, object]
ASGIScope = dict[str, object]
Receive = Callable[[], Awaitable[ASGIMessage]]
Send = Callable[[ASGIMessage], Awaitable[None]]
ASGIApp = Callable[[ASGIScope, Receive, Send], Awaitable[None]]


class ForbiddenError(TenantCoreError):
    def __init__(
        self,
        message: str = "Недостаточно роли для операции",
        *,
        details: dict[str, object] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        super().__init__(
            status_code=403,
            error_code=FORBIDDEN_CODE,
            message=message,
            details=details,
            correlation_id=correlation_id,
        )


@dataclass(frozen=True, slots=True)
class AccessPolicy:
    """Deny-by-default RBAC policy for a protected operation."""

    allowed_roles: tuple[str, ...]
    action: str
    resource_type: str = "request"

    def __post_init__(self) -> None:
        _require_non_empty(self.action, "action")
        _require_non_empty(self.resource_type, "resource_type")
        object.__setattr__(
            self,
            "allowed_roles",
            _normalize_allowed_roles(self.allowed_roles),
        )
        object.__setattr__(self, "action", self.action.strip())
        object.__setattr__(self, "resource_type", self.resource_type.strip())

    @classmethod
    def allow_roles(
        cls,
        *roles: str,
        action: str,
        resource_type: str = "request",
    ) -> AccessPolicy:
        return cls(
            allowed_roles=roles,
            action=action,
            resource_type=resource_type,
        )

    @classmethod
    def deny_all(
        cls,
        *,
        action: str,
        resource_type: str = "request",
    ) -> AccessPolicy:
        return cls(
            allowed_roles=(),
            action=action,
            resource_type=resource_type,
        )

    def error_details(self) -> dict[str, object]:
        return {
            "action": self.action,
            "resource_type": self.resource_type,
            "required_roles": list(self.allowed_roles),
        }


@dataclass(frozen=True, slots=True)
class EndpointAccessPolicy:
    """RBAC policy bound to an HTTP method and route template."""

    method: str
    path_template: str
    access_policy: AccessPolicy

    def __post_init__(self) -> None:
        _require_non_empty(self.method, "method")
        _require_non_empty(self.path_template, "path_template")
        method = self.method.strip().upper()
        path_template = self.path_template.strip()
        if not path_template.startswith("/"):
            raise ValueError("path_template должен начинаться с /")

        object.__setattr__(self, "method", method)
        object.__setattr__(self, "path_template", path_template)

    def matches(self, *, method: str, path: str) -> bool:
        return self.method == method.upper() and _path_template_matches(
            self.path_template,
            path,
        )


class RBACASGIMiddleware:
    """ASGI endpoint guard that enforces configured RBAC policies."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        endpoint_policies: Iterable[EndpointAccessPolicy],
        default_policy: AccessPolicy | None = None,
    ) -> None:
        self._app = app
        self._endpoint_policies = tuple(endpoint_policies)
        self._default_policy = default_policy or AccessPolicy.deny_all(
            action="endpoint.unlisted",
            resource_type="endpoint",
        )

    async def __call__(
        self,
        scope: ASGIScope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        policy = self._policy_for_scope(scope)
        try:
            require_access(policy)
        except TenantCoreError as error:
            await _send_error(send, error)
            return

        await self._app(scope, receive, send)

    def _policy_for_scope(self, scope: ASGIScope) -> AccessPolicy:
        method = _scope_string(scope, "method").upper()
        path = _scope_string(scope, "path")
        for endpoint_policy in self._endpoint_policies:
            if endpoint_policy.matches(method=method, path=path):
                return endpoint_policy.access_policy

        return self._default_policy


def require_access(
    policy: AccessPolicy,
    *,
    context: TenantContext | None = None,
) -> TenantContext:
    resolved_context = context or require_tenant_context()
    actor_roles = frozenset(
        role for role in resolved_context.roles if role in GOVERNANCE_ROLE_SET
    )

    if actor_roles.intersection(policy.allowed_roles):
        return resolved_context

    raise ForbiddenError(
        details=policy.error_details(),
        correlation_id=resolved_context.correlation_id,
    )


def require_any_role(
    *roles: str,
    context: TenantContext | None = None,
    action: str,
    resource_type: str = "request",
) -> TenantContext:
    return require_access(
        AccessPolicy.allow_roles(
            *roles,
            action=action,
            resource_type=resource_type,
        ),
        context=context,
    )


def _normalize_allowed_roles(roles: Iterable[str]) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(roles))
    invalid_roles = [
        role
        for role in normalized
        if not isinstance(role, str) or role not in GOVERNANCE_ROLE_SET
    ]
    if invalid_roles:
        raise ValueError(
            "allowed_roles содержит неизвестные роли RBAC: "
            + ", ".join(str(role) for role in invalid_roles)
        )

    return normalized


def _require_non_empty(value: str, field_name: str) -> None:
    if value.strip() == "":
        raise ValueError(f"{field_name} должен быть непустой строкой")


def _path_template_matches(template: str, path: str) -> bool:
    template_parts = _split_path(template)
    path_parts = _split_path(path)
    if len(template_parts) != len(path_parts):
        return False

    return all(
        _path_part_matches(template_part, path_part)
        for template_part, path_part in zip(template_parts, path_parts, strict=True)
    )


def _path_part_matches(template_part: str, path_part: str) -> bool:
    if template_part.startswith("{") and template_part.endswith("}"):
        return len(template_part) > 2 and path_part != ""

    return template_part == path_part


def _split_path(value: str) -> tuple[str, ...]:
    normalized = value.strip()
    if normalized == "/":
        return ()

    return tuple(part for part in normalized.strip("/").split("/") if part)


def _scope_string(scope: ASGIScope, key: str) -> str:
    value = scope.get(key)
    if isinstance(value, str):
        return value

    return ""


async def _send_error(send: Send, error: TenantCoreError) -> None:
    body = json.dumps(error.to_response_body(), ensure_ascii=False).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": error.status_code,
            "headers": [(b"content-type", b"application/json; charset=utf-8")],
        }
    )
    await send({"type": "http.response.body", "body": body})


BLOCKCHAIN_AUDIT_ENDPOINT_POLICIES: tuple[EndpointAccessPolicy, ...] = (
    EndpointAccessPolicy(
        method="POST",
        path_template="/audit/record",
        access_policy=AccessPolicy.allow_roles(
            COUNCIL_ROLE,
            action="blockchain_audit.record",
            resource_type="blockchain_auditor",
        ),
    ),
    EndpointAccessPolicy(
        method="POST",
        path_template="/audit/verify",
        access_policy=AccessPolicy.allow_roles(
            COUNCIL_ROLE,
            action="blockchain_audit.verify",
            resource_type="blockchain_auditor",
        ),
    ),
    EndpointAccessPolicy(
        method="GET",
        path_template="/audit/records/{event_id}",
        access_policy=AccessPolicy.allow_roles(
            COUNCIL_ROLE,
            action="blockchain_audit.read",
            resource_type="blockchain_auditor",
        ),
    ),
)
