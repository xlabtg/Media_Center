from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from policy_manager.api import (
        POLICY_MANAGER_SERVICE_NAME,
        ApplyPoliciesRequest,
        PolicyManagerAPIState,
        UpdatePolicyRequest,
        create_policy_manager_app,
    )
    from policy_manager.manager import (
        DEFAULT_POLICY_AUDIT_HASH,
        DEFAULT_POLICY_VALUES,
        POLICY_KEY_PATTERN,
        POLICY_MANAGER_SCHEMA_VERSION,
        POLICY_MANAGER_SOURCE,
        POLICY_UPDATED_EVENT,
        InMemoryPolicyRepository,
        PolicyApplicationResult,
        PolicyApplyInput,
        PolicyDecision,
        PolicyHistoryResponse,
        PolicyListResponse,
        PolicyManager,
        PolicyManagerError,
        PolicyNotFoundError,
        PolicyRecord,
        PolicyUpdateInput,
        normalize_policy_key,
        subject_ref_hash,
    )

_EXPORTS: dict[str, str] = {
    "POLICY_MANAGER_SERVICE_NAME": "policy_manager.api",
    "ApplyPoliciesRequest": "policy_manager.api",
    "PolicyManagerAPIState": "policy_manager.api",
    "UpdatePolicyRequest": "policy_manager.api",
    "create_policy_manager_app": "policy_manager.api",
    "DEFAULT_POLICY_AUDIT_HASH": "policy_manager.manager",
    "DEFAULT_POLICY_VALUES": "policy_manager.manager",
    "POLICY_KEY_PATTERN": "policy_manager.manager",
    "POLICY_MANAGER_SCHEMA_VERSION": "policy_manager.manager",
    "POLICY_MANAGER_SOURCE": "policy_manager.manager",
    "POLICY_UPDATED_EVENT": "policy_manager.manager",
    "InMemoryPolicyRepository": "policy_manager.manager",
    "PolicyApplicationResult": "policy_manager.manager",
    "PolicyApplyInput": "policy_manager.manager",
    "PolicyDecision": "policy_manager.manager",
    "PolicyHistoryResponse": "policy_manager.manager",
    "PolicyListResponse": "policy_manager.manager",
    "PolicyManager": "policy_manager.manager",
    "PolicyManagerError": "policy_manager.manager",
    "PolicyNotFoundError": "policy_manager.manager",
    "PolicyRecord": "policy_manager.manager",
    "PolicyUpdateInput": "policy_manager.manager",
    "normalize_policy_key": "policy_manager.manager",
    "subject_ref_hash": "policy_manager.manager",
}

__all__ = [
    "DEFAULT_POLICY_AUDIT_HASH",
    "DEFAULT_POLICY_VALUES",
    "POLICY_KEY_PATTERN",
    "POLICY_MANAGER_SCHEMA_VERSION",
    "POLICY_MANAGER_SERVICE_NAME",
    "POLICY_MANAGER_SOURCE",
    "POLICY_UPDATED_EVENT",
    "ApplyPoliciesRequest",
    "InMemoryPolicyRepository",
    "PolicyApplicationResult",
    "PolicyApplyInput",
    "PolicyDecision",
    "PolicyHistoryResponse",
    "PolicyListResponse",
    "PolicyManager",
    "PolicyManagerAPIState",
    "PolicyManagerError",
    "PolicyNotFoundError",
    "PolicyRecord",
    "PolicyUpdateInput",
    "UpdatePolicyRequest",
    "create_policy_manager_app",
    "normalize_policy_key",
    "subject_ref_hash",
]


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
