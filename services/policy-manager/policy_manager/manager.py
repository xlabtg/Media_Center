from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import ConfigDict, Field, field_validator

from libs.shared.audit_logger import AuditLogger
from libs.shared.events import EventEnvelope, EventPublisher, InMemoryEventBus
from libs.shared.models import (
    AuditHash,
    IdempotencyKey,
    JSONValue,
    SharedBaseModel,
    SubjectId,
    TenantId,
)

POLICY_MANAGER_SOURCE = "policy-manager"
POLICY_MANAGER_SCHEMA_VERSION = "1.0"
POLICY_UPDATED_EVENT = "policy.updated"

DEFAULT_POLICY_AUDIT_HASH = "0" * 64
POLICY_KEY_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){0,7}$"

_POLICY_KEY_RE = re.compile(POLICY_KEY_PATTERN)


class PolicyManagerError(RuntimeError):
    """Base error for Policy Manager domain rule violations."""


class PolicyNotFoundError(PolicyManagerError):
    """Raised when a tenant policy key is unknown."""


class PolicyDecision(StrEnum):
    ALLOW = "allow"
    ESCALATE = "escalate"


class PolicyRecord(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    tenant_id: TenantId
    key: str = Field(min_length=1, max_length=128, pattern=POLICY_KEY_PATTERN)
    value: dict[str, JSONValue] = Field(default_factory=dict)
    version: int = Field(ge=1)
    updated_by: SubjectId | None = None
    updated_at: datetime
    audit_hash: AuditHash = DEFAULT_POLICY_AUDIT_HASH
    metadata: dict[str, JSONValue] = Field(default_factory=dict)

    @field_validator("updated_at")
    @classmethod
    def _normalize_updated_at(cls, value: datetime) -> datetime:
        return normalize_datetime(value)


class PolicyListResponse(SharedBaseModel):
    items: tuple[PolicyRecord, ...]


class PolicyHistoryResponse(SharedBaseModel):
    items: tuple[PolicyRecord, ...]


class PolicyApplicationResult(SharedBaseModel):
    tenant_id: TenantId
    decision: PolicyDecision
    policy_versions: dict[str, int] = Field(default_factory=dict)
    reasons: tuple[str, ...] = Field(default_factory=tuple)
    applied_at: datetime

    @field_validator("applied_at")
    @classmethod
    def _normalize_applied_at(cls, value: datetime) -> datetime:
        return normalize_datetime(value)


@dataclass(frozen=True, slots=True)
class PolicyUpdateInput:
    value: Mapping[str, JSONValue]
    metadata: Mapping[str, JSONValue] | None = None


@dataclass(frozen=True, slots=True)
class PolicyApplyInput:
    policy_keys: Sequence[str]
    facts: Mapping[str, JSONValue]


@dataclass(slots=True)
class InMemoryPolicyRepository:
    _history: dict[tuple[str, str], list[PolicyRecord]] = field(default_factory=dict)

    def list_policies(self, *, tenant_id: str) -> tuple[PolicyRecord, ...]:
        records = {
            key: _default_policy_record(tenant_id=tenant_id, key=key)
            for key in DEFAULT_POLICY_VALUES
        }
        for (record_tenant_id, key), history in self._history.items():
            if record_tenant_id == tenant_id and history:
                records[key] = history[-1]

        return tuple(sorted(records.values(), key=lambda policy: policy.key))

    def get_policy(self, *, tenant_id: str, key: str) -> PolicyRecord:
        normalized_key = normalize_policy_key(key)
        history = self._history.get(_policy_key(tenant_id, normalized_key))
        if history:
            return history[-1]
        if normalized_key in DEFAULT_POLICY_VALUES:
            return _default_policy_record(tenant_id=tenant_id, key=normalized_key)

        raise PolicyNotFoundError(f"Политика {normalized_key} не найдена")

    def get_history(self, *, tenant_id: str, key: str) -> tuple[PolicyRecord, ...]:
        normalized_key = normalize_policy_key(key)
        history = tuple(self._history.get(_policy_key(tenant_id, normalized_key), ()))
        if normalized_key in DEFAULT_POLICY_VALUES:
            return (
                _default_policy_record(tenant_id=tenant_id, key=normalized_key),
                *history,
            )
        if history:
            return history

        raise PolicyNotFoundError(f"История политики {normalized_key} не найдена")

    def save_policy(self, policy: PolicyRecord) -> PolicyRecord:
        key = _policy_key(policy.tenant_id, policy.key)
        self._history.setdefault(key, []).append(policy)
        return policy


@dataclass(slots=True)
class PolicyManager:
    publisher: EventPublisher = field(default_factory=InMemoryEventBus)
    repository: InMemoryPolicyRepository = field(
        default_factory=InMemoryPolicyRepository
    )
    audit_logger: AuditLogger = field(default_factory=AuditLogger)

    def list_policies(self, *, tenant_id: str) -> tuple[PolicyRecord, ...]:
        return self.repository.list_policies(tenant_id=tenant_id)

    def get_history(self, *, tenant_id: str, key: str) -> tuple[PolicyRecord, ...]:
        return self.repository.get_history(tenant_id=tenant_id, key=key)

    async def update_policy(
        self,
        *,
        tenant_id: str,
        key: str,
        updated_by: str,
        correlation_id: str,
        update: PolicyUpdateInput,
        updated_at: datetime | str | None = None,
        event_id: str | None = None,
    ) -> PolicyRecord:
        normalized_key = normalize_policy_key(key)
        changed_at = normalize_datetime(updated_at or datetime.now(UTC))
        value = _clone_json_object(update.value)
        metadata = _clone_json_object(update.metadata or {})
        existing = _existing_or_none(
            repository=self.repository,
            tenant_id=tenant_id,
            key=normalized_key,
        )
        version = 1 if existing is None else existing.version + 1
        actor_hash = subject_ref_hash(tenant_id=tenant_id, subject_id=updated_by)
        audit_record = self.audit_logger.record(
            event_type=POLICY_UPDATED_EVENT,
            tenant_id=tenant_id,
            metadata={
                "key": normalized_key,
                "version": version,
                "value": value,
                "metadata": metadata,
            },
            timestamp=changed_at,
            correlation_id=correlation_id,
            actor_hash=actor_hash,
            source=POLICY_MANAGER_SOURCE,
        )
        policy = PolicyRecord(
            tenant_id=tenant_id,
            key=normalized_key,
            value=value,
            version=version,
            updated_by=updated_by,
            updated_at=changed_at,
            audit_hash=audit_record.audit_hash,
            metadata=metadata,
        )
        self.repository.save_policy(policy)

        await self.publisher.publish(
            EventEnvelope(
                event_id=event_id or _new_id("evt-policy-updated"),
                type=POLICY_UPDATED_EVENT,
                schema_version=POLICY_MANAGER_SCHEMA_VERSION,
                tenant_id=tenant_id,
                source=POLICY_MANAGER_SOURCE,
                correlation_id=correlation_id,
                occurred_at=changed_at,
                payload={
                    "key": policy.key,
                    "version": policy.version,
                    "audit_hash": policy.audit_hash,
                },
            )
        )
        return policy

    def apply_policies(
        self,
        *,
        tenant_id: str,
        application: PolicyApplyInput,
        applied_at: datetime | str | None = None,
    ) -> PolicyApplicationResult:
        if not application.policy_keys:
            raise PolicyManagerError("Нужно указать хотя бы одну политику")

        facts = _clone_json_object(application.facts)
        policy_versions: dict[str, int] = {}
        reasons: list[str] = []
        for key in application.policy_keys:
            policy = self.repository.get_policy(tenant_id=tenant_id, key=key)
            policy_versions[policy.key] = policy.version
            reasons.extend(_policy_reasons(policy=policy, facts=facts))

        unique_reasons = tuple(dict.fromkeys(reasons))
        decision = PolicyDecision.ESCALATE if unique_reasons else PolicyDecision.ALLOW
        return PolicyApplicationResult(
            tenant_id=tenant_id,
            decision=decision,
            policy_versions=policy_versions,
            reasons=unique_reasons,
            applied_at=normalize_datetime(applied_at or datetime.now(UTC)),
        )


def normalize_policy_key(key: str) -> str:
    normalized = key.strip()
    if not _POLICY_KEY_RE.fullmatch(normalized):
        raise ValueError("policy key должен быть dot-separated lower_snake_case")

    return normalized


def subject_ref_hash(*, tenant_id: str, subject_id: str) -> str:
    return "sha256:" + hashlib.sha256(f"{tenant_id}:{subject_id}".encode()).hexdigest()


def normalize_datetime(value: datetime | str) -> datetime:
    if isinstance(value, str):
        normalized = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        normalized = value

    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=UTC)

    return normalized.astimezone(UTC)


def _existing_or_none(
    *,
    repository: InMemoryPolicyRepository,
    tenant_id: str,
    key: str,
) -> PolicyRecord | None:
    try:
        return repository.get_policy(tenant_id=tenant_id, key=key)
    except PolicyNotFoundError:
        return None


def _policy_reasons(
    *,
    policy: PolicyRecord,
    facts: Mapping[str, JSONValue],
) -> tuple[str, ...]:
    kind = _string_field(policy.value, "kind", default="metadata")
    if kind == "threshold":
        return _threshold_policy_reasons(policy.value, facts=facts)
    if kind == "flag":
        return _flag_policy_reasons(policy.value, facts=facts)

    return ()


def _threshold_policy_reasons(
    value: Mapping[str, JSONValue],
    *,
    facts: Mapping[str, JSONValue],
) -> tuple[str, ...]:
    target = _string_field(value, "target")
    operator = _string_field(value, "operator")
    observed = _number_field(facts, target)
    violation = _threshold_violation(value, operator=operator, observed=observed)
    if not violation or _decision_on_violation(value) is PolicyDecision.ALLOW:
        return ()

    return (_string_field(value, "reason", default=f"{target}_policy_violation"),)


def _threshold_violation(
    value: Mapping[str, JSONValue],
    *,
    operator: str,
    observed: float,
) -> bool:
    if operator == "lte":
        return observed > _number_field(value, "threshold")
    if operator == "lt":
        return observed >= _number_field(value, "threshold")
    if operator == "gte":
        return observed < _number_field(value, "threshold")
    if operator == "gt":
        return observed <= _number_field(value, "threshold")
    if operator == "between":
        return not (
            _number_field(value, "min") <= observed <= _number_field(value, "max")
        )

    raise PolicyManagerError(f"Оператор политики {operator} не поддержан")


def _flag_policy_reasons(
    value: Mapping[str, JSONValue],
    *,
    facts: Mapping[str, JSONValue],
) -> tuple[str, ...]:
    target = _string_field(value, "target")
    expected = value.get("expected")
    if expected is None:
        raise PolicyManagerError("flag-политика должна содержать expected")
    if (
        facts.get(target) == expected
        or _decision_on_violation(value) is PolicyDecision.ALLOW
    ):
        return ()

    return (_string_field(value, "reason", default=f"{target}_policy_violation"),)


def _decision_on_violation(value: Mapping[str, JSONValue]) -> PolicyDecision:
    decision = _string_field(
        value,
        "decision_on_violation",
        default=PolicyDecision.ESCALATE.value,
    )
    if decision == PolicyDecision.ALLOW.value:
        return PolicyDecision.ALLOW
    if decision == PolicyDecision.ESCALATE.value:
        return PolicyDecision.ESCALATE

    raise PolicyManagerError("decision_on_violation должен быть allow или escalate")


def _string_field(
    value: Mapping[str, JSONValue],
    key: str,
    *,
    default: str | None = None,
) -> str:
    raw_value = value.get(key)
    if raw_value is None:
        if default is not None:
            return default
        raise PolicyManagerError(f"Политика должна содержать строковое поле {key}")
    if not isinstance(raw_value, str) or raw_value.strip() == "":
        raise PolicyManagerError(f"Поле политики {key} должно быть непустой строкой")

    return raw_value.strip()


def _number_field(value: Mapping[str, JSONValue], key: str) -> float:
    raw_value = value.get(key)
    if isinstance(raw_value, bool) or not isinstance(raw_value, int | float):
        raise PolicyManagerError(f"Поле {key} должно быть числом")

    return float(raw_value)


def _default_policy_record(*, tenant_id: str, key: str) -> PolicyRecord:
    return PolicyRecord(
        tenant_id=tenant_id,
        key=key,
        value=_clone_json_object(DEFAULT_POLICY_VALUES[key]),
        version=1,
        updated_at=datetime(1970, 1, 1, tzinfo=UTC),
        metadata={"source": "default"},
    )


def _clone_json_object(value: Mapping[str, JSONValue]) -> dict[str, JSONValue]:
    return deepcopy(dict(value))


def _policy_key(tenant_id: str, key: str) -> tuple[str, str]:
    return tenant_id, key


def _new_id(prefix: str) -> IdempotencyKey:
    return f"{prefix}-{uuid4()}"


DEFAULT_POLICY_VALUES: dict[str, dict[str, JSONValue]] = {
    "automation.max_autonomous_risk_score": {
        "kind": "threshold",
        "target": "risk_score",
        "operator": "lte",
        "threshold": 0.7,
        "reason": "risk_score_above_threshold",
        "decision_on_violation": "escalate",
    },
    "automation.min_agent_confidence": {
        "kind": "threshold",
        "target": "agent_confidence",
        "operator": "gte",
        "threshold": 0.65,
        "reason": "confidence_below_threshold",
        "decision_on_violation": "escalate",
    },
    "hitl.veto_window_hours": {
        "kind": "threshold",
        "target": "veto_window_hours",
        "operator": "between",
        "min": 4,
        "max": 12,
        "default": 8,
        "reason": "veto_window_out_of_range",
        "decision_on_violation": "escalate",
    },
    "rl_kpi.min_precision": {
        "kind": "threshold",
        "target": "rl_precision",
        "operator": "gte",
        "threshold": 0.75,
        "reason": "rl_precision_below_threshold",
        "decision_on_violation": "escalate",
    },
    "ethics.require_xai": {
        "kind": "flag",
        "target": "has_xai",
        "expected": True,
        "reason": "xai_required",
        "decision_on_violation": "escalate",
    },
}
