from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from blockchain_auditor import (
    AuditRecordCommand,
    BlockchainAuditorAPIState,
    GrpcBlockchainAuditConnector,
    HashGenerationResult,
    InMemoryGrpcBlockchainAuditTransport,
    build_blockchain_auditor_settings,
    create_blockchain_auditor_app,
    generate_event_hash,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

from libs.shared import ServiceTemplateConfig, TenantContext, encode_hs256_jwt

ROOT = Path(__file__).resolve().parents[1]
JWT_SECRET = "blockchain-auditor-test-secret"


def _app() -> FastAPI:
    config = ServiceTemplateConfig(
        service_name="blockchain-auditor",
        version="0.1.0",
        jwt_secret=JWT_SECRET,
        prometheus_enabled=True,
    )
    settings = build_blockchain_auditor_settings(
        {"BLOCKCHAIN_AUDITOR_URL": "grpc://besu-auditor.internal:50051"}
    )
    connector = GrpcBlockchainAuditConnector(
        settings=settings,
        transport=InMemoryGrpcBlockchainAuditTransport(),
    )
    return create_blockchain_auditor_app(config, connector=connector)


def _client(app: FastAPI | None = None) -> TestClient:
    return TestClient(app or _app())


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
        "X-Correlation-Id": f"corr-{tenant_id}",
    }


def test_blockchain_auditor_openapi_documents_verify_endpoint() -> None:
    client = _client()

    response = client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert "/audit/verify" in schema["paths"]
    assert schema["paths"]["/audit/verify"].keys() >= {"get", "post"}


def test_audit_event_verification_confirms_matching_recorded_hash() -> None:
    app = _app()
    client = _client(app)
    generated = _record_audit_event(app, points=27.0)

    response = client.post(
        "/audit/verify",
        headers=_headers(),
        json={
            "event_id": "evt-contribution-1",
            "event_type": "contribution.recorded",
            "timestamp": "2026-06-18T12:00:00Z",
            "points": 27.0,
            "metadata": {
                "source": "contribution-ledger",
                "schema_version": "1.0",
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["matched"] is True
    assert body["mismatch_reason"] is None
    assert body["tenant_id"] == "tenant-a"
    assert body["event_id"] == "evt-contribution-1"
    assert body["recorded_hash"] == generated.audit_hash
    assert body["calculated_hash"] == generated.audit_hash
    assert body["block_ref"] == (
        "grpc://besu-auditor.internal:50051/audit/tenant-a/evt-contribution-1"
    )


def test_audit_event_verification_reports_hash_mismatch() -> None:
    app = _app()
    client = _client(app)
    generated = _record_audit_event(app, points=27.0)

    response = client.post(
        "/audit/verify",
        headers=_headers(),
        json={
            "event_id": "evt-contribution-1",
            "event_type": "contribution.recorded",
            "timestamp": "2026-06-18T12:00:00Z",
            "points": 28.0,
            "metadata": {
                "source": "contribution-ledger",
                "schema_version": "1.0",
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["matched"] is False
    assert body["mismatch_reason"] == "hash_mismatch"
    assert body["recorded_hash"] == generated.audit_hash
    assert body["calculated_hash"] != generated.audit_hash


def test_audit_hash_verification_supports_get_contract() -> None:
    app = _app()
    client = _client(app)
    generated = _record_audit_event(app, points=27.0)

    response = client.get(
        "/audit/verify",
        headers=_headers(),
        params={
            "event_id": "evt-contribution-1",
            "hash": generated.audit_hash,
        },
    )

    assert response.status_code == 200
    assert response.json()["matched"] is True


def test_audit_verification_returns_404_for_missing_record() -> None:
    client = _client()

    response = client.post(
        "/audit/verify",
        headers=_headers(),
        json={
            "event_id": "evt-missing",
            "event_type": "contribution.recorded",
            "timestamp": "2026-06-18T12:00:00Z",
            "points": 27.0,
            "metadata": {"source": "contribution-ledger"},
        },
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "audit_record_not_found"


def test_audit_verification_requires_council_role() -> None:
    app = _app()
    client = _client(app)
    _record_audit_event(app, points=27.0)

    response = client.get(
        "/audit/verify",
        headers=_headers(subject="member-1", roles=("member_full",)),
        params={
            "event_id": "evt-contribution-1",
            "hash": "a" * 64,
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_issue_51_blockchain_auditor_docs_are_marked_implemented() -> None:
    spec = (ROOT / "docs/modules/blockchain-auditor.md").read_text(encoding="utf-8")
    readme = (ROOT / "services/blockchain-auditor/README.md").read_text(
        encoding="utf-8"
    )

    for marker in (
        "API верификации audit records",
        "POST** `/audit/verify`",
        "GET** `/audit/verify?event_id=&hash=`",
        "matched",
    ):
        assert marker in spec

    for marker in (
        "create_blockchain_auditor_app",
        "POST /audit/verify",
        "GET /audit/verify?event_id=&hash=",
        "audit_record_not_found",
    ):
        assert marker in readme


def _record_audit_event(app: FastAPI, *, points: float) -> HashGenerationResult:
    generated = generate_event_hash(
        event_type="contribution.recorded",
        tenant_id="tenant-a",
        points=points,
        metadata={
            "source": "contribution-ledger",
            "schema_version": "1.0",
        },
        timestamp="2026-06-18T12:00:00Z",
    )
    state = cast(BlockchainAuditorAPIState, app.state.blockchain_auditor_api)

    async def record() -> None:
        await state.connector.record_audit_hash(
            AuditRecordCommand(
                tenant_id="tenant-a",
                event_id="evt-contribution-1",
                event_type="contribution.recorded",
                audit_hash=generated.audit_hash,
                occurred_at=datetime(2026, 6, 18, 12, 0, tzinfo=UTC),
                correlation_id="corr-audit-1",
                metadata={
                    "source": "contribution-ledger",
                    "schema_version": "1.0",
                },
            ),
            context=state_context(),
        )

    asyncio.run(record())
    return generated


def state_context() -> TenantContext:
    return TenantContext(
        tenant_id="tenant-a",
        subject="council-1",
        roles=("council",),
        correlation_id="corr-audit-1",
    )
