from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from threading import Lock
from typing import Protocol

from libs.shared.errors import RATE_LIMITED_CODE
from libs.shared.tenant import TENANT_ID_PATTERN, TenantContext, TenantCoreError

Clock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class TenantResourcePlan:
    """Capacity limits applied independently to one tenant."""

    name: str
    request_limit: int
    window_seconds: int
    concurrent_operations: int
    storage_bytes: int
    queue_depth: int

    def __post_init__(self) -> None:
        normalized_name = self.name.strip()
        if normalized_name == "":
            raise ValueError("name должен быть непустой строкой")
        if self.window_seconds <= 0:
            raise ValueError("window_seconds должен быть положительным")

        for field_name, value in (
            ("request_limit", self.request_limit),
            ("concurrent_operations", self.concurrent_operations),
            ("storage_bytes", self.storage_bytes),
            ("queue_depth", self.queue_depth),
        ):
            if value < 0:
                raise ValueError(f"{field_name} не может быть отрицательным")

        object.__setattr__(self, "name", normalized_name)


@dataclass(frozen=True, slots=True)
class TenantResourceSnapshot:
    tenant_id: str
    plan_name: str
    window_started_at: int
    window_seconds: int
    request_limit: int
    requests_used: int
    concurrent_operations_limit: int
    concurrent_operations: int
    storage_bytes_limit: int
    storage_bytes_used: int
    queue_depth_limit: int
    queue_items: int

    @property
    def requests_remaining(self) -> int:
        return max(self.request_limit - self.requests_used, 0)

    @property
    def operation_slots_remaining(self) -> int:
        return max(
            self.concurrent_operations_limit - self.concurrent_operations,
            0,
        )

    @property
    def storage_bytes_remaining(self) -> int:
        return max(self.storage_bytes_limit - self.storage_bytes_used, 0)

    @property
    def queue_slots_remaining(self) -> int:
        return max(self.queue_depth_limit - self.queue_items, 0)


@dataclass(frozen=True, slots=True)
class TenantResourceDecision:
    allowed: bool
    tenant_id: str
    plan_name: str
    reason: str | None
    retry_after_seconds: int
    service_name: str | None
    operation: str | None
    resource_type: str
    requested_amount: int
    snapshot: TenantResourceSnapshot


class TenantResourceManager(Protocol):
    def admit_request(
        self,
        context: TenantContext,
        *,
        service_name: str,
        operation: str,
    ) -> TenantResourceDecision:
        """Record one tenant request when the active plan allows it."""

    def acquire_operation_slot(
        self,
        context: TenantContext,
        *,
        operation: str,
    ) -> TenantResourceDecision:
        """Reserve one concurrent execution slot for the tenant."""

    def release_operation_slot(
        self,
        context: TenantContext,
        *,
        operation: str,
    ) -> TenantResourceSnapshot:
        """Release one concurrent execution slot for the tenant."""


class TenantResourceLimitError(TenantCoreError):
    def __init__(
        self,
        decision: TenantResourceDecision,
        *,
        correlation_id: str | None,
    ) -> None:
        super().__init__(
            status_code=429,
            error_code=RATE_LIMITED_CODE,
            message="Превышена квота ресурсов tenant",
            details={
                "reason": decision.reason,
                "plan": decision.plan_name,
                "resource_type": decision.resource_type,
                "retry_after_seconds": decision.retry_after_seconds,
            },
            correlation_id=correlation_id,
        )
        self.decision = decision


@dataclass(slots=True)
class _TenantResourceState:
    plan: TenantResourcePlan
    window_started_at: int
    requests_used: int = 0
    concurrent_operations: int = 0
    storage_bytes_used: int = 0
    queue_items: int = 0


class InMemoryTenantResourceManager:
    """Thread-safe tenant resource manager for tests and local service wiring."""

    def __init__(
        self,
        *,
        default_plan: TenantResourcePlan,
        tenant_plans: Mapping[str, TenantResourcePlan] | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._default_plan = default_plan
        self._tenant_plans = {
            _validated_tenant_id(tenant_id): plan
            for tenant_id, plan in (tenant_plans or {}).items()
        }
        self._clock = clock or time.time
        self._lock = Lock()
        self._states: dict[str, _TenantResourceState] = {}

    def configure_tenant(self, tenant_id: str, plan: TenantResourcePlan) -> None:
        normalized_tenant_id = _validated_tenant_id(tenant_id)
        with self._lock:
            self._tenant_plans[normalized_tenant_id] = plan
            state = self._states.get(normalized_tenant_id)
            if state is not None:
                state.plan = plan
                self._roll_window_if_needed(state, plan)

    def plan_for_tenant(self, tenant_id: str) -> TenantResourcePlan:
        normalized_tenant_id = _validated_tenant_id(tenant_id)
        with self._lock:
            return self._tenant_plans.get(normalized_tenant_id, self._default_plan)

    def admit_request(
        self,
        context: TenantContext,
        *,
        service_name: str,
        operation: str,
    ) -> TenantResourceDecision:
        normalized_service = _normalized_token(service_name, "service_name")
        normalized_operation = _normalized_token(operation, "operation")
        with self._lock:
            state = self._state_for_context(context)
            plan = state.plan
            reset_at = state.window_started_at + plan.window_seconds
            retry_after = max(1, reset_at - int(self._clock()))

            if state.requests_used >= plan.request_limit:
                return self._decision(
                    allowed=False,
                    context=context,
                    state=state,
                    reason="request_limit_exceeded",
                    retry_after_seconds=retry_after,
                    service_name=normalized_service,
                    operation=normalized_operation,
                    resource_type="request",
                    requested_amount=1,
                )

            state.requests_used += 1
            return self._decision(
                allowed=True,
                context=context,
                state=state,
                reason=None,
                retry_after_seconds=0,
                service_name=normalized_service,
                operation=normalized_operation,
                resource_type="request",
                requested_amount=1,
            )

    def acquire_operation_slot(
        self,
        context: TenantContext,
        *,
        operation: str,
    ) -> TenantResourceDecision:
        normalized_operation = _normalized_token(operation, "operation")
        with self._lock:
            state = self._state_for_context(context)
            plan = state.plan
            if state.concurrent_operations >= plan.concurrent_operations:
                return self._decision(
                    allowed=False,
                    context=context,
                    state=state,
                    reason="concurrency_limit_exceeded",
                    retry_after_seconds=1,
                    service_name=None,
                    operation=normalized_operation,
                    resource_type="concurrent_operation",
                    requested_amount=1,
                )

            state.concurrent_operations += 1
            return self._decision(
                allowed=True,
                context=context,
                state=state,
                reason=None,
                retry_after_seconds=0,
                service_name=None,
                operation=normalized_operation,
                resource_type="concurrent_operation",
                requested_amount=1,
            )

    def release_operation_slot(
        self,
        context: TenantContext,
        *,
        operation: str,
    ) -> TenantResourceSnapshot:
        _normalized_token(operation, "operation")
        with self._lock:
            state = self._state_for_context(context)
            state.concurrent_operations = max(state.concurrent_operations - 1, 0)
            return self._snapshot(context.tenant_id, state)

    def reserve_queue_items(
        self,
        context: TenantContext,
        *,
        queue_name: str,
        amount: int = 1,
    ) -> TenantResourceDecision:
        normalized_queue = _normalized_token(queue_name, "queue_name")
        normalized_amount = _positive_amount(amount, "amount")
        with self._lock:
            state = self._state_for_context(context)
            plan = state.plan
            if state.queue_items + normalized_amount > plan.queue_depth:
                return self._decision(
                    allowed=False,
                    context=context,
                    state=state,
                    reason="queue_depth_exceeded",
                    retry_after_seconds=1,
                    service_name=None,
                    operation=normalized_queue,
                    resource_type="queue",
                    requested_amount=normalized_amount,
                )

            state.queue_items += normalized_amount
            return self._decision(
                allowed=True,
                context=context,
                state=state,
                reason=None,
                retry_after_seconds=0,
                service_name=None,
                operation=normalized_queue,
                resource_type="queue",
                requested_amount=normalized_amount,
            )

    def release_queue_items(
        self,
        context: TenantContext,
        *,
        queue_name: str,
        amount: int = 1,
    ) -> TenantResourceSnapshot:
        _normalized_token(queue_name, "queue_name")
        normalized_amount = _positive_amount(amount, "amount")
        with self._lock:
            state = self._state_for_context(context)
            state.queue_items = max(state.queue_items - normalized_amount, 0)
            return self._snapshot(context.tenant_id, state)

    def reserve_storage_bytes(
        self,
        context: TenantContext,
        *,
        amount: int,
        resource_type: str,
    ) -> TenantResourceDecision:
        normalized_amount = _positive_amount(amount, "amount")
        normalized_resource = _normalized_token(resource_type, "resource_type")
        with self._lock:
            state = self._state_for_context(context)
            plan = state.plan
            if state.storage_bytes_used + normalized_amount > plan.storage_bytes:
                return self._decision(
                    allowed=False,
                    context=context,
                    state=state,
                    reason="storage_quota_exceeded",
                    retry_after_seconds=0,
                    service_name=None,
                    operation=None,
                    resource_type=normalized_resource,
                    requested_amount=normalized_amount,
                )

            state.storage_bytes_used += normalized_amount
            return self._decision(
                allowed=True,
                context=context,
                state=state,
                reason=None,
                retry_after_seconds=0,
                service_name=None,
                operation=None,
                resource_type=normalized_resource,
                requested_amount=normalized_amount,
            )

    def release_storage_bytes(
        self,
        context: TenantContext,
        *,
        amount: int,
        resource_type: str,
    ) -> TenantResourceSnapshot:
        _normalized_token(resource_type, "resource_type")
        normalized_amount = _positive_amount(amount, "amount")
        with self._lock:
            state = self._state_for_context(context)
            state.storage_bytes_used = max(
                state.storage_bytes_used - normalized_amount,
                0,
            )
            return self._snapshot(context.tenant_id, state)

    def snapshot(self, context: TenantContext) -> TenantResourceSnapshot:
        with self._lock:
            state = self._state_for_context(context)
            return self._snapshot(context.tenant_id, state)

    def _state_for_context(self, context: TenantContext) -> _TenantResourceState:
        tenant_id = _validated_tenant_id(context.tenant_id)
        plan = self._tenant_plans.get(tenant_id, self._default_plan)
        state = self._states.get(tenant_id)
        if state is None:
            state = _TenantResourceState(
                plan=plan,
                window_started_at=self._window_started_at(plan),
            )
            self._states[tenant_id] = state
            return state

        if state.plan is not plan:
            state.plan = plan
        self._roll_window_if_needed(state, plan)
        return state

    def _roll_window_if_needed(
        self,
        state: _TenantResourceState,
        plan: TenantResourcePlan,
    ) -> None:
        window_started_at = self._window_started_at(plan)
        if state.window_started_at != window_started_at:
            state.window_started_at = window_started_at
            state.requests_used = 0

    def _window_started_at(self, plan: TenantResourcePlan) -> int:
        now = int(self._clock())
        return now - (now % plan.window_seconds)

    def _decision(
        self,
        *,
        allowed: bool,
        context: TenantContext,
        state: _TenantResourceState,
        reason: str | None,
        retry_after_seconds: int,
        service_name: str | None,
        operation: str | None,
        resource_type: str,
        requested_amount: int,
    ) -> TenantResourceDecision:
        return TenantResourceDecision(
            allowed=allowed,
            tenant_id=context.tenant_id,
            plan_name=state.plan.name,
            reason=reason,
            retry_after_seconds=retry_after_seconds,
            service_name=service_name,
            operation=operation,
            resource_type=resource_type,
            requested_amount=requested_amount,
            snapshot=self._snapshot(context.tenant_id, state),
        )

    def _snapshot(
        self,
        tenant_id: str,
        state: _TenantResourceState,
    ) -> TenantResourceSnapshot:
        plan = state.plan
        return TenantResourceSnapshot(
            tenant_id=tenant_id,
            plan_name=plan.name,
            window_started_at=state.window_started_at,
            window_seconds=plan.window_seconds,
            request_limit=plan.request_limit,
            requests_used=state.requests_used,
            concurrent_operations_limit=plan.concurrent_operations,
            concurrent_operations=state.concurrent_operations,
            storage_bytes_limit=plan.storage_bytes,
            storage_bytes_used=state.storage_bytes_used,
            queue_depth_limit=plan.queue_depth,
            queue_items=state.queue_items,
        )


def _validated_tenant_id(tenant_id: str) -> str:
    normalized_tenant_id = tenant_id.strip()
    if TENANT_ID_PATTERN.fullmatch(normalized_tenant_id) is None:
        raise ValueError("tenant_id имеет недопустимый формат")

    return normalized_tenant_id


def _normalized_token(value: str, label: str) -> str:
    normalized = value.strip()
    if normalized == "":
        raise ValueError(f"{label} должен быть непустой строкой")

    return normalized


def _positive_amount(value: int, label: str) -> int:
    if value <= 0:
        raise ValueError(f"{label} должен быть положительным")

    return value
