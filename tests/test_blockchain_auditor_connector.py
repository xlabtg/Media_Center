from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime

import pytest
from blockchain_auditor import (
    AuditMetadataPolicyError,
    AuditRecordCommand,
    BlockchainAuditorSettings,
    GrpcBlockchainAuditConnector,
    InMemoryGrpcBlockchainAuditTransport,
    build_blockchain_auditor_settings,
    generate_event_hash,
)


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

    receipt = await connector.record_audit_hash(command)
    stored = await connector.get_audit_record(
        tenant_id="tenant-a",
        event_id="evt-contribution-1",
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
        await connector.record_audit_hash(command)

    assert transport.record_requests == ()
