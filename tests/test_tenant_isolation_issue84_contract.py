from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from typing import cast

import pytest

from libs.shared import (
    AuditLogger,
    InMemoryAuditLogSink,
    InMemoryAuditSink,
    InMemoryTenantCache,
    InMemoryTenantObjectStorage,
    InMemoryTenantVectorStore,
    JSONValue,
    ObservabilityContext,
    TenantAuditEvent,
    TenantIsolationError,
    TenantScopedRepository,
    TenantScopedSQLAlchemyRepository,
    TenantSetting,
    TenantTestIdentity,
    VectorRecord,
    assert_only_tenant_records,
    build_structured_log_entry,
    build_tenant_test_dataset,
    format_structured_log,
)

NOW = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)


def test_issue84_database_layer_denies_cross_tenant_records_with_403() -> None:
    dataset = build_tenant_test_dataset()
    owner_context = dataset.owner.context()
    tenant_records = TenantScopedRepository[dict[str, object]]("tenant_records")

    owner_records = tenant_records.list_for_tenant(dataset.all_records, owner_context)

    assert owner_records == list(dataset.owner_records)
    assert_only_tenant_records(owner_records, dataset.owner.tenant_id)
    _assert_no_foreign_leak(
        json.dumps(owner_records, ensure_ascii=False, sort_keys=True),
        foreign=dataset.foreign,
        extra_forbidden=("contribution-foreign-1",),
    )

    sql_repository = TenantScopedSQLAlchemyRepository(
        TenantSetting,
        resource_type="tenant_settings",
    )
    tenant_sql = str(
        sql_repository.statement_for_tenant(owner_context).compile(
            compile_kwargs={"literal_binds": True}
        )
    )
    foreign_setting = TenantSetting(
        id="setting-foreign",
        tenant_id=dataset.foreign.tenant_id,
        key="feature.search",
        value_json={"enabled": True},
        version=1,
        updated_by=dataset.foreign.subject,
    )
    audit_sink = InMemoryAuditSink()

    assert "tenant_settings.tenant_id = 'tenant-a'" in tenant_sql
    with pytest.raises(TenantIsolationError) as exc_info:
        sql_repository.require_owned(
            foreign_setting,
            owner_context,
            audit_sink=audit_sink,
        )

    _assert_403(exc_info.value)
    event = _single_audit_event(audit_sink)
    assert event.resource_type == "tenant_settings"
    _assert_no_foreign_leak(
        json.dumps(event.as_dict(), ensure_ascii=False, sort_keys=True),
        foreign=dataset.foreign,
        extra_forbidden=("setting-foreign",),
    )


def test_issue84_storage_layers_keep_foreign_data_invisible() -> None:
    asyncio.run(_run_storage_layer_contract())


async def _run_storage_layer_contract() -> None:
    dataset = build_tenant_test_dataset()
    owner_context = dataset.owner.context()
    foreign_context = dataset.foreign.context()
    vector_store = InMemoryTenantVectorStore()
    object_storage = InMemoryTenantObjectStorage(bucket="nmc-dev")
    cache = InMemoryTenantCache()
    audit_sink = InMemoryAuditSink()

    await vector_store.upsert(
        "memory",
        [
            VectorRecord(
                id="doc-owner",
                embedding=(0.0, 0.0, 1.0),
                document="owner planning note",
                metadata={"source": "owner"},
            )
        ],
        context=owner_context,
    )
    await vector_store.upsert(
        "memory",
        [
            VectorRecord(
                id="doc-foreign",
                embedding=(1.0, 0.0, 0.0),
                document="foreign private note",
                metadata={"source": "foreign"},
            )
        ],
        context=foreign_context,
    )

    owner_vectors = await vector_store.query(
        "memory",
        (0.0, 0.0, 0.9),
        context=owner_context,
        limit=5,
    )
    with pytest.raises(TenantIsolationError) as vector_exc:
        await vector_store.query(
            "memory",
            (0.0, 0.0, 0.9),
            context=owner_context,
            metadata_filter={"tenant_id": dataset.foreign.tenant_id},
            audit_sink=audit_sink,
        )

    assert [result.id for result in owner_vectors] == ["doc-owner"]
    _assert_no_foreign_leak(
        json.dumps(
            [
                {
                    "id": result.id,
                    "document": result.document,
                    "metadata": dict(result.metadata),
                }
                for result in owner_vectors
            ],
            ensure_ascii=False,
            sort_keys=True,
        ),
        foreign=dataset.foreign,
        extra_forbidden=("doc-foreign", "foreign private note"),
    )
    _assert_403(vector_exc.value)

    await object_storage.put_object(
        "content",
        "post-owner.bin",
        b"owner payload",
        metadata={"source": "owner"},
        context=owner_context,
    )
    await object_storage.put_object(
        "content",
        "post-foreign.bin",
        b"foreign payload",
        metadata={"source": "foreign"},
        context=foreign_context,
    )

    owner_object = await object_storage.get_object(
        "content",
        "post-owner.bin",
        context=owner_context,
    )
    foreign_object_from_owner_context = await object_storage.get_object(
        "content",
        "post-foreign.bin",
        context=owner_context,
    )
    owner_object_refs = await object_storage.list_objects(
        "content",
        context=owner_context,
    )
    with pytest.raises(TenantIsolationError) as object_exc:
        await object_storage.create_presigned_put_url(
            "content",
            "post-cross.bin",
            content_hash=hashlib.sha256(b"cross payload").hexdigest(),
            metadata={"tenant_id": dataset.foreign.tenant_id},
            context=owner_context,
            audit_sink=audit_sink,
        )

    assert owner_object is not None
    assert owner_object.body == b"owner payload"
    assert foreign_object_from_owner_context is None
    assert [item.object_id for item in owner_object_refs] == ["post-owner.bin"]
    _assert_no_foreign_leak(
        json.dumps(
            {
                "body": owner_object.body.decode("utf-8"),
                "refs": [item.object_id for item in owner_object_refs],
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        foreign=dataset.foreign,
        extra_forbidden=("post-foreign.bin", "foreign payload"),
    )
    _assert_403(object_exc.value)

    await cache.set_json(
        "profiles",
        "member",
        {"display_name": "owner-profile"},
        context=owner_context,
    )
    await cache.set_json(
        "profiles",
        "member",
        {"display_name": "foreign-profile"},
        context=foreign_context,
    )

    owner_profile = await cache.get_json("profiles", "member", context=owner_context)
    assert owner_profile == {"display_name": "owner-profile"}
    _assert_no_foreign_leak(
        json.dumps(owner_profile, ensure_ascii=False, sort_keys=True),
        foreign=dataset.foreign,
        extra_forbidden=("foreign-profile",),
    )

    deleted_owner_keys = await cache.invalidate_namespace(
        "profiles",
        context=owner_context,
    )

    assert deleted_owner_keys == 1
    assert await cache.get_json("profiles", "member", context=owner_context) is None
    assert await cache.get_json("profiles", "member", context=foreign_context) == {
        "display_name": "foreign-profile"
    }

    resource_types = {event.resource_type for event in audit_sink.events}
    assert {"vector_filter", "object_metadata"} <= resource_types
    for event in audit_sink.events:
        assert event.status_code == 403
        assert event.error_code == "tenant_isolation_violation"
        assert event.tenant_id == dataset.owner.tenant_id
        _assert_no_foreign_leak(
            json.dumps(event.as_dict(), ensure_ascii=False, sort_keys=True),
            foreign=dataset.foreign,
            extra_forbidden=("doc-foreign", "post-foreign.bin", "foreign payload"),
        )


def test_issue84_isolation_audit_and_logs_do_not_disclose_foreign_tenant() -> None:
    dataset = build_tenant_test_dataset()
    owner_context = dataset.owner.context()
    audit_sink = InMemoryAuditSink()
    repository = TenantScopedRepository[dict[str, object]]("audit_probe")

    with pytest.raises(TenantIsolationError) as exc_info:
        repository.require_owned(
            {
                "tenant_id": dataset.foreign.tenant_id,
                "record_id": "foreign-secret-record",
                "subject": dataset.foreign.subject,
            },
            owner_context,
            audit_sink=audit_sink,
        )

    _assert_403(exc_info.value)
    tenant_event = _single_audit_event(audit_sink)
    event_payload = cast(dict[str, JSONValue], tenant_event.as_dict())
    audit_log_sink = InMemoryAuditLogSink()
    audit_record = AuditLogger(
        sink=audit_log_sink,
        clock=lambda: NOW,
    ).record(
        event_type=tenant_event.event_type,
        tenant_id=owner_context.tenant_id,
        metadata=event_payload,
        correlation_id=owner_context.correlation_id,
        actor_hash=tenant_event.actor_hash,
        source="issue84-tenant-isolation-contract",
    )
    structured_log = format_structured_log(
        build_structured_log_entry(
            level="warning",
            message="tenant isolation denied",
            context=ObservabilityContext.from_tenant_context(
                owner_context,
                service_name="security",
                operation="tenant_isolation_denial",
            ),
            payload=event_payload,
            timestamp=NOW,
        )
    )

    assert audit_log_sink.records == (audit_record,)
    assert tenant_event.requested_tenant_hash is not None
    assert tenant_event.requested_tenant_hash != dataset.foreign.tenant_id
    assert tenant_event.actor_hash is not None
    assert tenant_event.actor_hash != owner_context.subject
    assert json.loads(structured_log)["payload"]["status_code"] == 403

    for raw_payload in (
        json.dumps(event_payload, ensure_ascii=False, sort_keys=True),
        audit_record.model_dump_json(),
        structured_log,
    ):
        _assert_no_foreign_leak(
            raw_payload,
            foreign=dataset.foreign,
            extra_forbidden=("foreign-secret-record",),
        )


def _assert_403(error: TenantIsolationError) -> None:
    assert error.status_code == 403
    assert error.error_code == "tenant_isolation_violation"


def _single_audit_event(audit_sink: InMemoryAuditSink) -> TenantAuditEvent:
    assert len(audit_sink.events) == 1
    return audit_sink.events[0]


def _assert_no_foreign_leak(
    raw_payload: str,
    *,
    foreign: TenantTestIdentity,
    extra_forbidden: tuple[str, ...] = (),
) -> None:
    forbidden_values = (
        foreign.tenant_id,
        foreign.subject,
        *extra_forbidden,
    )
    leaked_values = [value for value in forbidden_values if value in raw_payload]

    assert leaked_values == []
