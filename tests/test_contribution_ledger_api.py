from __future__ import annotations

from typing import cast

from contribution_ledger import (
    ContributionLedgerAPIState,
    create_contribution_ledger_app,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

from libs.shared import ServiceTemplateConfig, encode_hs256_jwt

JWT_SECRET = "contribution-ledger-test-secret"


def _app() -> FastAPI:
    config = ServiceTemplateConfig(
        service_name="contribution-ledger",
        version="0.1.0",
        jwt_secret=JWT_SECRET,
        prometheus_enabled=True,
    )
    return create_contribution_ledger_app(config)


def _client() -> TestClient:
    return TestClient(_app())


def _headers(
    *,
    tenant_id: str = "tenant-a",
    subject: str = "member-1",
    idempotency_key: str | None = None,
) -> dict[str, str]:
    token = encode_hs256_jwt(
        {
            "tenant_id": tenant_id,
            "sub": subject,
            "roles": ["member_full"],
        },
        JWT_SECRET,
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Tenant-Id": tenant_id,
        "X-Correlation-Id": f"corr-{tenant_id}",
    }
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key

    return headers


def test_contribution_ledger_openapi_documents_public_endpoints() -> None:
    client = _client()

    response = client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert schema["paths"].keys() >= {
        "/contributions",
        "/weights",
        "/weights/recalculate",
        "/payout-distribution",
    }


def test_contribution_registration_weights_and_distribution_flow() -> None:
    client = _client()

    contribution = client.post(
        "/contributions",
        headers=_headers(idempotency_key="contribution-1"),
        json={
            "member_id": "member-a",
            "event_type": "content_creation",
            "source_type": "publication",
            "source_ref": "publication-1",
            "platform": "telegram",
            "reach": 100_000,
            "occurred_at": "2026-06-18T12:00:00Z",
            "metadata": {"campaign": "pilot"},
        },
    )

    assert contribution.status_code == 201
    contribution_body = contribution.json()
    assert contribution_body["tenant_id"] == "tenant-a"
    assert contribution_body["member_id"] == "member-a"
    assert contribution_body["points_awarded"] == 27.0
    assert len(contribution_body["audit_hash"]) == 64

    client.post(
        "/contributions",
        headers=_headers(idempotency_key="contribution-2"),
        json={
            "member_id": "member-b",
            "event_type": "publish",
            "source_type": "publication",
            "source_ref": "publication-2",
            "platform": "vk",
            "occurred_at": "2026-06-20T09:00:00Z",
        },
    )

    recalculated = client.post(
        "/weights/recalculate",
        headers=_headers(idempotency_key="weights-2026-06"),
        json={"period": "2026-06", "avg_points_council": 100},
    )

    assert recalculated.status_code == 200
    weight_snapshot = recalculated.json()
    assert weight_snapshot["tenant_id"] == "tenant-a"
    assert weight_snapshot["period"] == "2026-06"
    assert weight_snapshot["total_kv_capped"] == 0.16
    assert [member["member_id"] for member in weight_snapshot["members"]] == [
        "member-a",
        "member-b",
    ]
    assert [member["kv_raw"] for member in weight_snapshot["members"]] == [0.27, 0.06]
    assert [member["kv_capped"] for member in weight_snapshot["members"]] == [
        0.1,
        0.06,
    ]
    assert [member["payout_share"] for member in weight_snapshot["members"]] == [
        0.625,
        0.375,
    ]

    weights = client.get(
        "/weights",
        headers=_headers(),
        params={"period": "2026-06"},
    )

    assert weights.status_code == 200
    assert weights.json()["calculation_hash"] == weight_snapshot["calculation_hash"]

    distribution = client.get(
        "/payout-distribution",
        headers=_headers(),
        params={"period": "2026-06"},
    )

    assert distribution.status_code == 200
    distribution_body = distribution.json()
    assert distribution_body["tenant_id"] == "tenant-a"
    assert distribution_body["member_count"] == 2
    assert distribution_body["total_payout_share"] == 1.0
    assert len(distribution_body["distribution_hash"]) == 64


def test_contribution_registration_is_idempotent_and_rejects_conflict() -> None:
    app = _app()
    client = TestClient(app)
    headers = _headers(idempotency_key="contribution-idempotent")
    payload = {
        "member_id": "member-a",
        "event_type": "idea",
        "source_type": "manual",
        "source_ref": "idea-idempotent",
        "occurred_at": "2026-06-18T12:00:00Z",
    }

    first = client.post("/contributions", headers=headers, json=payload)
    second = client.post("/contributions", headers=headers, json=payload)

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["contribution_id"] == first.json()["contribution_id"]
    state = cast(ContributionLedgerAPIState, app.state.contribution_ledger_api)
    assert len(state.publisher.messages) == 2

    conflict = client.post(
        "/contributions",
        headers=headers,
        json={**payload, "source_ref": "another-idea"},
    )

    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"


def test_contribution_ledger_returns_validation_error_envelope() -> None:
    client = _client()

    response = client.post(
        "/contributions",
        headers=_headers(idempotency_key="invalid-contribution"),
        json={
            "member_id": "member-a",
            "event_type": "unknown",
            "source_type": "manual",
            "source_ref": "idea-invalid",
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


def test_contribution_ledger_returns_403_for_tenant_override() -> None:
    app = _app()
    client = TestClient(app)
    headers = _headers(idempotency_key="contribution-cross-tenant")
    headers["X-Tenant-Id"] = "tenant-b"

    response = client.post(
        "/contributions",
        headers=headers,
        json={
            "member_id": "member-a",
            "event_type": "idea",
            "source_type": "manual",
            "source_ref": "idea-1",
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "tenant_isolation_violation"
    state = cast(ContributionLedgerAPIState, app.state.contribution_ledger_api)
    assert state.audit_sink.events[0].event_type == "tenant.isolation_violation"
    assert state.audit_sink.events[0].tenant_id == "tenant-a"
