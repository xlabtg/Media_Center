from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from blockchain_auditor import (
    AuditBatchWriter,
    AuditMetadataPolicyError,
    AuditRecordCommand,
    BlockchainAuditorSettings,
    GrpcBlockchainAuditConnector,
    InMemoryGrpcBlockchainAuditTransport,
    build_blockchain_auditor_settings,
    generate_event_hash,
)

from libs.shared import (
    ForbiddenError,
    JSONValue,
    TenantContext,
    TenantIsolationError,
)

ROOT = Path(__file__).resolve().parents[1]


def test_hash_generator_uses_sha256_and_sorted_canonical_json() -> None:
    timestamp = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    expected_payload = {
        "event_type": "contribution.recorded",
        "tenant_id": "tenant-a",
        "points": 27.0,
        "metadata": {
            "nested": {"a": 1, "b": 2},
            "source": "contribution-ledger",
        },
        "timestamp": "2026-06-18T12:00:00Z",
    }
    expected_hash = hashlib.sha256(
        json.dumps(expected_payload, sort_keys=True).encode("utf-8")
    ).hexdigest()

    first = generate_event_hash(
        event_type="contribution.recorded",
        tenant_id="tenant-a",
        points=27.0,
        metadata={"source": "contribution-ledger", "nested": {"b": 2, "a": 1}},
        timestamp=timestamp,
    )
    second = generate_event_hash(
        event_type="contribution.recorded",
        tenant_id="tenant-a",
        points=27.0,
        metadata={"nested": {"a": 1, "b": 2}, "source": "contribution-ledger"},
        timestamp="2026-06-18T12:00:00+00:00",
    )

    assert first.algorithm == "sha256"
    assert first.audit_hash == expected_hash
    assert second.audit_hash == expected_hash
    assert json.loads(first.canonical_json) == expected_payload


def test_settings_reads_blockchain_auditor_url_from_env() -> None:
    settings = build_blockchain_auditor_settings(
        {
            "BLOCKCHAIN_AUDITOR_URL": "grpc://besu-auditor.internal:50051",
            "SERVICE_NAME": "private-auditor",
            "SERVICE_VERSION": "0.2.0",
        }
    )

    assert isinstance(settings, BlockchainAuditorSettings)
    assert settings.blockchain_auditor_url == "grpc://besu-auditor.internal:50051"
    assert settings.service_name == "private-auditor"
    assert settings.version == "0.2.0"


def test_grpc_connector_records_and_reads_hash_only_audit_record() -> None:
    asyncio.run(_run_grpc_connector_scenario())


async def _run_grpc_connector_scenario() -> None:
    settings = build_blockchain_auditor_settings(
        {"BLOCKCHAIN_AUDITOR_URL": "grpc://besu-auditor.internal:50051"}
    )
    transport = InMemoryGrpcBlockchainAuditTransport()
    connector = GrpcBlockchainAuditConnector(settings=settings, transport=transport)
    context = _council_context()
    generated = generate_event_hash(
        event_type="contribution.recorded",
        tenant_id="tenant-a",
        points=27.0,
        metadata={"source": "contribution-ledger"},
        timestamp="2026-06-18T12:00:00Z",
    )
    command = AuditRecordCommand(
        tenant_id="tenant-a",
        event_id="evt-contribution-1",
        event_type="contribution.recorded",
        audit_hash=generated.audit_hash,
        occurred_at=datetime(2026, 6, 18, 12, 0, tzinfo=UTC),
        correlation_id="corr-audit-1",
        metadata={
            "source": "contribution-ledger",
            "schema_version": "1.0",
            "contribution_id": "contribution-1",
        },
    )

    receipt = await connector.record_audit_hash(command, context=context)
    stored = await connector.get_audit_record(
        tenant_id="tenant-a",
        event_id="evt-contribution-1",
        context=context,
    )

    assert receipt.audit_hash == generated.audit_hash
    assert receipt.block_ref == (
        "grpc://besu-auditor.internal:50051/audit/tenant-a/evt-contribution-1"
    )
    assert stored is not None
    assert stored.event_id == "evt-contribution-1"
    assert stored.audit_hash == generated.audit_hash
    assert stored.metadata == {
        "source": "contribution-ledger",
        "schema_version": "1.0",
        "contribution_id": "contribution-1",
    }
    assert transport.record_requests[0].metadata == command.metadata
    assert "points" not in transport.record_requests[0].metadata


def test_grpc_connector_rejects_sensitive_metadata_before_network_call() -> None:
    asyncio.run(_run_sensitive_metadata_scenario())


async def _run_sensitive_metadata_scenario() -> None:
    settings = build_blockchain_auditor_settings(
        {"BLOCKCHAIN_AUDITOR_URL": "grpc://besu-auditor.internal:50051"}
    )
    transport = InMemoryGrpcBlockchainAuditTransport()
    connector = GrpcBlockchainAuditConnector(settings=settings, transport=transport)
    command = AuditRecordCommand(
        tenant_id="tenant-a",
        event_id="evt-payout-1",
        event_type="payout.executed",
        audit_hash="a" * 64,
        occurred_at=datetime(2026, 6, 18, 12, 0, tzinfo=UTC),
        correlation_id="corr-audit-1",
        metadata={"payout_share": 0.25},
    )

    with pytest.raises(AuditMetadataPolicyError, match="payout_share"):
        await connector.record_audit_hash(command, context=_council_context())

    assert transport.record_requests == ()


def test_grpc_connector_allows_only_council_context_and_matching_tenant() -> None:
    asyncio.run(_run_access_control_scenario())


async def _run_access_control_scenario() -> None:
    settings = build_blockchain_auditor_settings(
        {"BLOCKCHAIN_AUDITOR_URL": "grpc://besu-auditor.internal:50051"}
    )
    transport = InMemoryGrpcBlockchainAuditTransport()
    connector = GrpcBlockchainAuditConnector(settings=settings, transport=transport)
    command = _audit_command(event_id="evt-access-1", tenant_id="tenant-a")

    with pytest.raises(ForbiddenError):
        await connector.record_audit_hash(
            command,
            context=TenantContext(
                tenant_id="tenant-a",
                subject="member-1",
                roles=("member_full",),
                correlation_id="corr-audit-1",
            ),
        )

    with pytest.raises(TenantIsolationError):
        await connector.record_audit_hash(
            command,
            context=_council_context(tenant_id="tenant-b"),
        )

    assert transport.record_requests == ()


def test_batch_writer_records_hashes_through_single_batch_call() -> None:
    asyncio.run(_run_batch_writer_scenario())


async def _run_batch_writer_scenario() -> None:
    settings = build_blockchain_auditor_settings(
        {"BLOCKCHAIN_AUDITOR_URL": "grpc://besu-auditor.internal:50051"}
    )
    transport = InMemoryGrpcBlockchainAuditTransport()
    connector = GrpcBlockchainAuditConnector(settings=settings, transport=transport)
    writer = AuditBatchWriter(connector=connector)
    context = _council_context()
    commands = (
        _audit_command(event_id="evt-batch-1", tenant_id="tenant-a"),
        _audit_command(
            event_id="evt-batch-2",
            tenant_id="tenant-a",
            audit_hash="b" * 64,
            metadata={"source": "contribution-ledger", "schema_version": "1.0"},
        ),
    )

    receipts = await writer.record_batch(commands, context=context)
    first = await connector.get_audit_record(
        tenant_id="tenant-a",
        event_id="evt-batch-1",
        context=context,
    )
    second = await connector.get_audit_record(
        tenant_id="tenant-a",
        event_id="evt-batch-2",
        context=context,
    )

    assert [receipt.event_id for receipt in receipts] == [
        "evt-batch-1",
        "evt-batch-2",
    ]
    assert first is not None
    assert first.audit_hash == "a" * 64
    assert second is not None
    assert second.metadata == {
        "source": "contribution-ledger",
        "schema_version": "1.0",
    }
    assert transport.record_requests == ()
    assert len(transport.batch_record_requests) == 1
    assert [command.event_id for command in transport.batch_record_requests[0]] == [
        "evt-batch-1",
        "evt-batch-2",
    ]


def test_batch_writer_rejects_invalid_commands_before_transport() -> None:
    asyncio.run(_run_batch_writer_rejection_scenario())


async def _run_batch_writer_rejection_scenario() -> None:
    settings = build_blockchain_auditor_settings(
        {"BLOCKCHAIN_AUDITOR_URL": "grpc://besu-auditor.internal:50051"}
    )
    transport = InMemoryGrpcBlockchainAuditTransport()
    connector = GrpcBlockchainAuditConnector(settings=settings, transport=transport)
    writer = AuditBatchWriter(connector=connector)
    context = _council_context()

    with pytest.raises(TenantIsolationError):
        await writer.record_batch(
            (
                _audit_command(event_id="evt-batch-1", tenant_id="tenant-a"),
                _audit_command(event_id="evt-batch-2", tenant_id="tenant-b"),
            ),
            context=context,
        )

    with pytest.raises(AuditMetadataPolicyError, match="email"):
        await writer.record_batch(
            (
                _audit_command(event_id="evt-batch-3", tenant_id="tenant-a"),
                _audit_command(
                    event_id="evt-batch-4",
                    tenant_id="tenant-a",
                    metadata={"actor": {"email": "member@example.test"}},
                ),
            ),
            context=context,
        )

    assert transport.batch_record_requests == ()


def test_issue_50_blockchain_auditor_docs_are_marked_implemented() -> None:
    spec = (ROOT / "docs/modules/blockchain-auditor.md").read_text(encoding="utf-8")
    readme = (ROOT / "services/blockchain-auditor/README.md").read_text(
        encoding="utf-8"
    )

    for marker in (
        "реализованы access_controller и batch_writer",
        "BlockchainAuditAccessController",
        "AuditBatchWriter",
        "record_audit_hashes()",
        "роль `council`",
    ):
        assert marker in spec

    for marker in (
        "access controller и batch writer",
        "BlockchainAuditAccessController",
        "AuditBatchWriter",
        "TenantContext",
    ):
        assert marker in readme


def _council_context(tenant_id: str = "tenant-a") -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        subject="council-1",
        roles=("council",),
        correlation_id="corr-audit-1",
    )


def _audit_command(
    *,
    event_id: str,
    tenant_id: str,
    audit_hash: str = "a" * 64,
    metadata: dict[str, JSONValue] | None = None,
) -> AuditRecordCommand:
    return AuditRecordCommand(
        tenant_id=tenant_id,
        event_id=event_id,
        event_type="contribution.recorded",
        audit_hash=audit_hash,
        occurred_at=datetime(2026, 6, 18, 12, 0, tzinfo=UTC),
        correlation_id="corr-audit-1",
        metadata=metadata or {"source": "unit-test"},
    )
