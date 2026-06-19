from __future__ import annotations

from pathlib import Path
from typing import cast

from blockchain_auditor import (
    BlockchainAuditorAPIState,
    GrpcBlockchainAuditConnector,
    InMemoryGrpcBlockchainAuditTransport,
    build_blockchain_auditor_settings,
    create_blockchain_auditor_app,
    generate_event_hash,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

from libs.shared import ServiceTemplateConfig, encode_hs256_jwt

ROOT = Path(__file__).resolve().parents[1]
JWT_SECRET = "blockchain-auditor-issue-52-secret"


def test_issue_52_private_blockchain_auditor_epic_acceptance_contract() -> None:
    app = _app()
    client = TestClient(app)
    first_hash = generate_event_hash(
        event_type="contribution.recorded",
        tenant_id="tenant-a",
        points=27.0,
        metadata={"source": "contribution-ledger", "schema_version": "1.0"},
        timestamp="2026-06-18T12:00:00Z",
    )
    second_hash = generate_event_hash(
        event_type="policy.updated",
        tenant_id="tenant-a",
        metadata={"source": "policy-manager", "schema_version": "1.0"},
        timestamp="2026-06-18T12:01:00Z",
    )

    recorded = client.post(
        "/audit/record",
        headers=_headers(),
        json={
            "records": [
                {
                    "event_id": "evt-issue-52-contribution",
                    "event_type": "contribution.recorded",
                    "audit_hash": first_hash.audit_hash,
                    "occurred_at": "2026-06-18T12:00:00Z",
                    "metadata": {
                        "source": "contribution-ledger",
                        "schema_version": "1.0",
                    },
                },
                {
                    "event_id": "evt-issue-52-policy",
                    "event_type": "policy.updated",
                    "audit_hash": second_hash.audit_hash,
                    "occurred_at": "2026-06-18T12:01:00Z",
                    "metadata": {"source": "policy-manager", "schema_version": "1.0"},
                },
            ]
        },
    )
    unsafe_record = client.post(
        "/audit/record",
        headers=_headers(),
        json={
            "records": [
                {
                    "event_id": "evt-issue-52-unsafe",
                    "event_type": "payout.executed",
                    "audit_hash": "c" * 64,
                    "occurred_at": "2026-06-18T12:02:00Z",
                    "metadata": {"amount": 1000, "source": "hitl-payout-gateway"},
                }
            ]
        },
    )
    unauthorized_record = client.post(
        "/audit/record",
        headers=_headers(subject="member-1", roles=("member_full",)),
        json={
            "records": [
                {
                    "event_id": "evt-issue-52-member",
                    "event_type": "contribution.recorded",
                    "audit_hash": "d" * 64,
                    "occurred_at": "2026-06-18T12:03:00Z",
                    "metadata": {"source": "unit-test"},
                }
            ]
        },
    )
    fetched = client.get(
        "/audit/records/evt-issue-52-contribution",
        headers=_headers(),
    )
    verified = client.post(
        "/audit/verify",
        headers=_headers(),
        json={
            "event_id": "evt-issue-52-contribution",
            "event_type": "contribution.recorded",
            "timestamp": "2026-06-18T12:00:00Z",
            "points": 27.0,
            "metadata": {
                "source": "contribution-ledger",
                "schema_version": "1.0",
            },
        },
    )

    assert recorded.status_code == 201
    recorded_body = recorded.json()
    assert [item["event_id"] for item in recorded_body["items"]] == [
        "evt-issue-52-contribution",
        "evt-issue-52-policy",
    ]
    assert unsafe_record.status_code == 400
    assert unsafe_record.json()["error"]["code"] == "audit_metadata_policy_violation"
    assert unauthorized_record.status_code == 403
    assert unauthorized_record.json()["error"]["code"] == "forbidden"

    assert fetched.status_code == 200
    fetched_body = fetched.json()
    assert fetched_body["event_id"] == "evt-issue-52-contribution"
    assert fetched_body["audit_hash"] == first_hash.audit_hash
    assert fetched_body["metadata"] == {
        "source": "contribution-ledger",
        "schema_version": "1.0",
    }

    assert verified.status_code == 200
    assert verified.json()["matched"] is True

    state = cast(BlockchainAuditorAPIState, app.state.blockchain_auditor_api)
    transport = cast(InMemoryGrpcBlockchainAuditTransport, state.connector.transport)
    assert transport.record_requests == ()
    assert len(transport.batch_record_requests) == 1
    assert [command.audit_hash for command in transport.batch_record_requests[0]] == [
        first_hash.audit_hash,
        second_hash.audit_hash,
    ]
    assert all(
        "amount" not in command.metadata and "member_id" not in command.metadata
        for command in transport.batch_record_requests[0]
    )


def test_issue_52_blockchain_auditor_docs_are_marked_implemented() -> None:
    spec = (ROOT / "docs/modules/blockchain-auditor.md").read_text(encoding="utf-8")
    readme = (ROOT / "services/blockchain-auditor/README.md").read_text(
        encoding="utf-8"
    )

    for marker in (
        "**Статус:** 🟢 реализовано",
        "POST** `/audit/record`",
        "GET** `/audit/records/{event_id}`",
        "#49",
        "#50",
        "#51",
        "#52",
        "Спецификация синхронизирована с реализацией Private Blockchain Auditor",
    ):
        assert marker in spec

    for marker in (
        "реализован сквозной контур Private Blockchain Auditor",
        "POST /audit/record",
        "GET /audit/records/{event_id}",
        "pytest tests/test_blockchain_auditor_epic_acceptance_contract.py",
    ):
        assert marker in readme


def _app() -> FastAPI:
    settings = build_blockchain_auditor_settings(
        {"BLOCKCHAIN_AUDITOR_URL": "grpc://besu-auditor.internal:50051"}
    )
    connector = GrpcBlockchainAuditConnector(
        settings=settings,
        transport=InMemoryGrpcBlockchainAuditTransport(),
    )
    return create_blockchain_auditor_app(
        ServiceTemplateConfig(
            service_name="blockchain-auditor",
            version="0.1.0",
            jwt_secret=JWT_SECRET,
            prometheus_enabled=True,
        ),
        connector=connector,
    )


def _headers(
    *,
    tenant_id: str = "tenant-a",
    subject: str = "council-1",
    roles: tuple[str, ...] = ("council",),
) -> dict[str, str]:
    token = encode_hs256_jwt(
        {
            "tenant_id": tenant_id,
            "sub": subject,
            "roles": list(roles),
        },
        JWT_SECRET,
    )
    return {
        "Authorization": f"Bearer {token}",
        "X-Tenant-Id": tenant_id,
        "X-Correlation-Id": "corr-issue-52",
    }
