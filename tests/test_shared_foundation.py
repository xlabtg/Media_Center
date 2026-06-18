from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from libs.shared import (
    AuditLogger,
    ErrorEnvelope,
    InMemoryAuditLogSink,
    RequestContextModel,
    TenantContext,
    TenantIsolationError,
    TenantScopedModel,
    audit_hash,
    tenant_context_from_trusted_headers,
    tenant_context_scope,
    tenant_headers_from_context,
)


def test_audit_logger_generates_sha256_from_canonical_payload() -> None:
    timestamp = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    metadata = {"contribution_id": "contrib-1", "source": "manual"}
    expected_payload = {
        "event_type": "contribution.recorded",
        "tenant_id": "tenant-a",
        "points": 10,
        "metadata": metadata,
        "timestamp": "2026-06-18T12:00:00Z",
    }
    expected_hash = hashlib.sha256(
        json.dumps(expected_payload, sort_keys=True).encode("utf-8")
    ).hexdigest()

    assert (
        audit_hash(
            event_type="contribution.recorded",
            tenant_id="tenant-a",
            points=10,
            metadata=metadata,
            timestamp=timestamp,
        )
        == expected_hash
    )

    sink = InMemoryAuditLogSink()
    logger = AuditLogger(sink=sink, clock=lambda: timestamp)
    record = logger.record(
        event_type="contribution.recorded",
        tenant_id="tenant-a",
        points=10,
        metadata=metadata,
        correlation_id="corr-shared-1",
    )

    assert record.audit_hash == expected_hash
    assert record.timestamp == timestamp
    assert sink.records == (record,)


def test_error_envelope_is_reusable_and_matches_tenant_errors() -> None:
    envelope = ErrorEnvelope.from_error(
        code="tenant_isolation_violation",
        message="Доступ к ресурсу другого tenant запрещён",
        details={"resource_type": "contributions"},
        correlation_id="corr-shared-2",
    )
    tenant_error = TenantIsolationError(
        details={"resource_type": "contributions"},
        correlation_id="corr-shared-2",
    )

    assert envelope.to_response_body() == tenant_error.to_response_body()
    assert envelope.error.code == "tenant_isolation_violation"


def test_pydantic_models_and_tenant_headers_roundtrip_context() -> None:
    context = TenantContext(
        tenant_id="tenant-a",
        subject="member-1",
        roles=("member_full", "council"),
        correlation_id="corr-shared-3",
    )
    headers = tenant_headers_from_context(
        context,
        service_name="api-gateway",
        forwarded_prefix="/contribution-ledger",
        original_path="/contribution-ledger/contributions",
    )

    restored_context = tenant_context_from_trusted_headers(headers)
    context_model = RequestContextModel.from_tenant_context(restored_context)
    tenant_model = TenantScopedModel(tenant_id=context_model.tenant_id)

    assert headers["x-tenant-id"] == "tenant-a"
    assert headers["x-subject-id"] == "member-1"
    assert headers["x-actor-roles"] == "member_full,council"
    assert restored_context == context
    assert context_model.to_tenant_context() == context
    assert tenant_model.tenant_id == "tenant-a"

    with tenant_context_scope(context):
        assert RequestContextModel.from_current_tenant_context() == context_model
