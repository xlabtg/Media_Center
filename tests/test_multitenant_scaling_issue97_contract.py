from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from threading import Lock

from libs.shared import (
    InMemoryTenantResourceManager,
    LoadTarget,
    TenantContext,
    TenantResourceDecision,
    TenantResourcePlan,
    TenantScopedRepository,
    assert_only_tenant_records,
    run_threaded_and_evaluate_load_scenario,
)

ROOT = Path(__file__).resolve().parents[1]


def test_issue97_parallel_tenants_keep_isolated_counters_under_load() -> None:
    manager = InMemoryTenantResourceManager(
        default_plan=TenantResourcePlan(
            name="scale-contract",
            request_limit=100,
            window_seconds=60,
            concurrent_operations=8,
            storage_bytes=16_384,
            queue_depth=16,
        ),
        clock=lambda: 1_800_000_000,
    )
    repository = TenantScopedRepository[dict[str, object]]("issue97_work_records")
    records: list[dict[str, object]] = []
    records_lock = Lock()

    def operation(iteration: int) -> bool:
        tenant_id = "tenant-a" if iteration % 2 == 0 else "tenant-b"
        context = TenantContext(
            tenant_id=tenant_id,
            subject=f"worker-{iteration % 4}",
            correlation_id=f"corr-issue-97-{iteration}",
        )
        request_decision = manager.admit_request(
            context,
            service_name="api-gateway",
            operation="route",
        )
        operation_decision = manager.acquire_operation_slot(
            context,
            operation="route",
        )
        queue_decision = manager.reserve_queue_items(
            context,
            queue_name="tenant-work",
            amount=1,
        )
        storage_decision = manager.reserve_storage_bytes(
            context,
            amount=64,
            resource_type="object_storage",
        )
        if not all(
            decision.allowed
            for decision in (
                request_decision,
                operation_decision,
                queue_decision,
                storage_decision,
            )
        ):
            if queue_decision.allowed:
                manager.release_queue_items(
                    context,
                    queue_name="tenant-work",
                    amount=1,
                )
            if operation_decision.allowed:
                manager.release_operation_slot(context, operation="route")
            return False

        try:
            with records_lock:
                records.append(
                    {
                        "id": f"work-{iteration}",
                        "tenant_id": tenant_id,
                        "sequence": iteration,
                    }
                )
            return True
        finally:
            manager.release_queue_items(
                context,
                queue_name="tenant-work",
                amount=1,
            )
            manager.release_operation_slot(context, operation="route")

    evaluation = run_threaded_and_evaluate_load_scenario(
        LoadTarget(
            name="issue97.multi_tenant_resource_isolation",
            operation_count=80,
            min_success_ratio=1,
        ),
        operation,
        max_workers=8,
    )

    assert evaluation.passed, evaluation.result.failure_details
    tenant_a_context = TenantContext(tenant_id="tenant-a", subject="auditor-a")
    tenant_b_context = TenantContext(tenant_id="tenant-b", subject="auditor-b")
    tenant_a_snapshot = manager.snapshot(tenant_a_context)
    tenant_b_snapshot = manager.snapshot(tenant_b_context)

    assert tenant_a_snapshot.requests_used == 40
    assert tenant_b_snapshot.requests_used == 40
    assert tenant_a_snapshot.storage_bytes_used == 40 * 64
    assert tenant_b_snapshot.storage_bytes_used == 40 * 64
    assert tenant_a_snapshot.concurrent_operations == 0
    assert tenant_b_snapshot.concurrent_operations == 0
    assert tenant_a_snapshot.queue_items == 0
    assert tenant_b_snapshot.queue_items == 0
    assert_only_tenant_records(
        repository.list_for_tenant(records, tenant_a_context),
        "tenant-a",
    )
    assert_only_tenant_records(
        repository.list_for_tenant(records, tenant_b_context),
        "tenant-b",
    )


def test_issue97_resource_denials_are_local_to_one_tenant() -> None:
    manager = InMemoryTenantResourceManager(
        default_plan=TenantResourcePlan(
            name="default",
            request_limit=20,
            window_seconds=60,
            concurrent_operations=4,
            storage_bytes=4_096,
            queue_depth=4,
        ),
        clock=lambda: 1_800_000_000,
    )
    tenant_a = TenantContext(tenant_id="tenant-a", subject="member-a")
    tenant_b = TenantContext(tenant_id="tenant-b", subject="member-b")
    manager.configure_tenant(
        "tenant-a",
        TenantResourcePlan(
            name="tenant-a-small",
            request_limit=2,
            window_seconds=60,
            concurrent_operations=1,
            storage_bytes=128,
            queue_depth=1,
        ),
    )

    tenant_a_request_results = tuple(
        manager.admit_request(
            tenant_a,
            service_name="api-gateway",
            operation="route",
        )
        for _ in range(3)
    )
    tenant_b_request_results = _admit_requests(manager, tenant_b, count=3)

    assert [decision.allowed for decision in tenant_a_request_results] == [
        True,
        True,
        False,
    ]
    assert tenant_a_request_results[-1].reason == "request_limit_exceeded"
    assert all(decision.allowed for decision in tenant_b_request_results)

    first_slot = manager.acquire_operation_slot(tenant_a, operation="publish")
    second_slot = manager.acquire_operation_slot(tenant_a, operation="publish")
    manager.release_operation_slot(tenant_a, operation="publish")
    released_slot = manager.acquire_operation_slot(tenant_a, operation="publish")
    manager.release_operation_slot(tenant_a, operation="publish")

    assert first_slot.allowed
    assert not second_slot.allowed
    assert second_slot.reason == "concurrency_limit_exceeded"
    assert released_slot.allowed

    first_queue = manager.reserve_queue_items(
        tenant_a,
        queue_name="publication",
        amount=1,
    )
    second_queue = manager.reserve_queue_items(
        tenant_a,
        queue_name="publication",
        amount=1,
    )
    manager.release_queue_items(tenant_a, queue_name="publication", amount=1)

    assert first_queue.allowed
    assert not second_queue.allowed
    assert second_queue.reason == "queue_depth_exceeded"

    first_storage = manager.reserve_storage_bytes(
        tenant_a,
        amount=96,
        resource_type="object_storage",
    )
    second_storage = manager.reserve_storage_bytes(
        tenant_a,
        amount=64,
        resource_type="object_storage",
    )
    tenant_b_storage = manager.reserve_storage_bytes(
        tenant_b,
        amount=512,
        resource_type="object_storage",
    )

    assert first_storage.allowed
    assert not second_storage.allowed
    assert second_storage.reason == "storage_quota_exceeded"
    assert tenant_b_storage.allowed
    assert manager.snapshot(tenant_a).storage_bytes_used == 96
    assert manager.snapshot(tenant_b).storage_bytes_used == 512


def test_issue97_scaling_contract_is_documented() -> None:
    scaling_doc = (ROOT / "docs/MULTITENANT_SCALING.md").read_text(
        encoding="utf-8",
    )
    gateway_doc = (ROOT / "services/api-gateway/README.md").read_text(
        encoding="utf-8",
    )
    tenant_doc = (ROOT / "docs/modules/tenant-isolation.md").read_text(
        encoding="utf-8",
    )

    for marker in (
        "#97",
        "TenantResourcePlan",
        "InMemoryTenantResourceManager",
        "request_limit",
        "concurrent_operations",
        "storage_bytes",
        "queue_depth",
        "tests/test_multitenant_scaling_issue97_contract.py",
    ):
        assert marker in scaling_doc

    assert "resource_manager" in gateway_doc
    assert "TenantResourcePlan" in tenant_doc


def _admit_requests(
    manager: InMemoryTenantResourceManager,
    context: TenantContext,
    *,
    count: int,
) -> Iterable[TenantResourceDecision]:
    return tuple(
        manager.admit_request(
            context,
            service_name="api-gateway",
            operation="route",
        )
        for _ in range(count)
    )
