from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from libs.shared.rbac import COUNCIL_ROLE, AccessPolicy, require_access
from libs.shared.tenant import TenantContext, assert_requested_tenant

BLOCKCHAIN_AUDITOR_RESOURCE_TYPE = "blockchain_auditor"
AUDIT_RECORD_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    action="blockchain_audit.record",
    resource_type=BLOCKCHAIN_AUDITOR_RESOURCE_TYPE,
)
AUDIT_BATCH_RECORD_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    action="blockchain_audit.batch_record",
    resource_type=BLOCKCHAIN_AUDITOR_RESOURCE_TYPE,
)
AUDIT_READ_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    action="blockchain_audit.read",
    resource_type=BLOCKCHAIN_AUDITOR_RESOURCE_TYPE,
)


@dataclass(frozen=True, slots=True)
class BlockchainAuditAccessController:
    """Council-only guard for private blockchain audit operations."""

    def require_record_access(
        self,
        *,
        tenant_id: str,
        context: TenantContext | None = None,
    ) -> TenantContext:
        return self._require_tenant_policy(
            tenant_id=tenant_id,
            context=context,
            policy=AUDIT_RECORD_POLICY,
        )

    def require_batch_record_access(
        self,
        *,
        tenant_ids: Iterable[str],
        context: TenantContext | None = None,
    ) -> TenantContext:
        resolved_context = require_access(AUDIT_BATCH_RECORD_POLICY, context=context)
        for tenant_id in tuple(dict.fromkeys(tenant_ids)):
            assert_requested_tenant(
                tenant_id,
                context=resolved_context,
                resource_type=BLOCKCHAIN_AUDITOR_RESOURCE_TYPE,
            )

        return resolved_context

    def require_read_access(
        self,
        *,
        tenant_id: str,
        context: TenantContext | None = None,
    ) -> TenantContext:
        return self._require_tenant_policy(
            tenant_id=tenant_id,
            context=context,
            policy=AUDIT_READ_POLICY,
        )

    def _require_tenant_policy(
        self,
        *,
        tenant_id: str,
        context: TenantContext | None,
        policy: AccessPolicy,
    ) -> TenantContext:
        resolved_context = require_access(policy, context=context)
        assert_requested_tenant(
            tenant_id,
            context=resolved_context,
            resource_type=BLOCKCHAIN_AUDITOR_RESOURCE_TYPE,
        )
        return resolved_context
