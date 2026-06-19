from __future__ import annotations

from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
from wallet import WalletAPIState, create_wallet_app

from libs.shared import ServiceTemplateConfig, encode_hs256_jwt

JWT_SECRET = "wallet-test-secret"


def _app() -> FastAPI:
    return create_wallet_app(
        ServiceTemplateConfig(
            service_name="wallet",
            version="0.1.0",
            jwt_secret=JWT_SECRET,
            prometheus_enabled=True,
        )
    )


def _client() -> TestClient:
    return TestClient(_app())


def _headers(
    *,
    tenant_id: str = "tenant-a",
    subject: str = "member-1",
    roles: tuple[str, ...] = ("member_full",),
    correlation_id: str = "corr-wallet-1",
    idempotency_key: str | None = None,
) -> dict[str, str]:
    token = encode_hs256_jwt(
        {
            "tenant_id": tenant_id,
            "sub": subject,
            "roles": list(roles),
        },
        JWT_SECRET,
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Tenant-Id": tenant_id,
        "X-Correlation-Id": correlation_id,
    }
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key

    return headers


def test_wallet_openapi_documents_public_endpoints() -> None:
    client = _client()

    response = client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert schema["paths"].keys() >= {
        "/wallet/operations",
        "/wallet/balance",
    }


def test_wallet_records_mcv_operation_and_returns_balance_history() -> None:
    app = _app()
    client = TestClient(app)

    credited = client.post(
        "/wallet/operations",
        headers=_headers(
            subject="council-1",
            roles=("council",),
            idempotency_key="wallet-credit-1",
        ),
        json={
            "operation_id": "wallet-op-1",
            "member_id": "member-a",
            "amount_mcv": "125.50",
            "type": "distribution_credit",
            "ref_type": "payout_distribution",
            "ref_id": "distribution-1",
            "period": "2026-06",
            "distribution_hash": "a" * 64,
            "payout_share": 0.625,
            "created_at": "2026-06-19T08:00:00Z",
            "metadata": {"source": "acceptance"},
        },
    )
    debited = client.post(
        "/wallet/operations",
        headers=_headers(
            subject="council-1",
            roles=("council",),
            idempotency_key="wallet-debit-1",
        ),
        json={
            "operation_id": "wallet-op-2",
            "member_id": "member-a",
            "amount_mcv": "-25.50",
            "type": "payout_debit",
            "ref_type": "payout",
            "ref_id": "payout-1",
            "period": "2026-06",
            "created_at": "2026-06-19T09:00:00Z",
        },
    )
    balance = client.get(
        "/wallet/balance",
        headers=_headers(subject="member-a"),
        params={"member_id": "member-a"},
    )
    operations = client.get(
        "/wallet/operations",
        headers=_headers(subject="member-a"),
        params={"member_id": "member-a"},
    )

    assert credited.status_code == 201
    credited_body = credited.json()
    assert credited_body["tenant_id"] == "tenant-a"
    assert credited_body["member_id"] == "member-a"
    assert credited_body["amount_mcv"] == "125.50"
    assert credited_body["balance_after_mcv"] == "125.50"
    assert credited_body["ref_type"] == "payout_distribution"
    assert credited_body["ref_id"] == "distribution-1"
    assert credited_body["distribution_hash"] == "a" * 64
    assert credited_body["payout_share"] == 0.625
    assert len(credited_body["audit_hash"]) == 64

    assert debited.status_code == 201
    assert debited.json()["balance_after_mcv"] == "100.00"

    assert balance.status_code == 200
    balance_body = balance.json()
    assert balance_body == {
        "tenant_id": "tenant-a",
        "member_id": "member-a",
        "balance_mcv": "100.00",
        "credited_mcv": "125.50",
        "debited_mcv": "25.50",
        "operation_count": 2,
    }

    assert operations.status_code == 200
    operation_items = operations.json()["items"]
    assert [item["operation_id"] for item in operation_items] == [
        "wallet-op-2",
        "wallet-op-1",
    ]
    assert operation_items[0]["balance_after_mcv"] == "100.00"
    assert operation_items[1]["balance_after_mcv"] == "125.50"

    state = cast(WalletAPIState, app.state.wallet_api)
    assert [record.event_type for record in state.audit_log_sink.records] == [
        "wallet.operation_recorded",
        "wallet.operation_recorded",
    ]
    assert [message.routing_key for message in state.publisher.messages] == [
        "tenant.tenant-a.wallet.operation_recorded",
        "tenant.tenant-a.wallet.operation_recorded",
    ]
    assert state.publisher.messages[0].envelope.payload == {
        "operation_id": "wallet-op-1",
        "member_hash": credited_body["member_hash"],
        "type": "distribution_credit",
        "ref_type": "payout_distribution",
        "ref_id": "distribution-1",
        "audit_hash": credited_body["audit_hash"],
    }


def test_wallet_operation_is_idempotent_and_rejects_conflict() -> None:
    client = _client()
    headers = _headers(
        subject="council-1",
        roles=("council",),
    )
    headers["Idempotency-Key"] = "wallet-idempotent-1"
    payload = {
        "operation_id": "wallet-op-idempotent",
        "member_id": "member-a",
        "amount_mcv": "10.00",
        "type": "manual_adjustment",
        "ref_type": "manual",
        "ref_id": "adjustment-1",
    }

    first = client.post("/wallet/operations", headers=headers, json=payload)
    second = client.post("/wallet/operations", headers=headers, json=payload)
    conflict = client.post(
        "/wallet/operations",
        headers=headers,
        json={**payload, "amount_mcv": "11.00"},
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["operation_id"] == first.json()["operation_id"]
    assert second.json()["audit_hash"] == first.json()["audit_hash"]
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"


def test_wallet_returns_403_for_tenant_override() -> None:
    app = _app()
    client = TestClient(app)
    headers = _headers(
        subject="council-1",
        roles=("council",),
        idempotency_key="wallet-cross-tenant",
    )
    headers["X-Tenant-Id"] = "tenant-b"

    response = client.post(
        "/wallet/operations",
        headers=headers,
        json={
            "operation_id": "wallet-op-cross-tenant",
            "member_id": "member-a",
            "amount_mcv": "10.00",
            "type": "manual_adjustment",
            "ref_type": "manual",
            "ref_id": "adjustment-1",
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "tenant_isolation_violation"
    state = cast(WalletAPIState, app.state.wallet_api)
    assert state.tenant_audit_sink.events[0].event_type == "tenant.isolation_violation"
    assert state.tenant_audit_sink.events[0].tenant_id == "tenant-a"
