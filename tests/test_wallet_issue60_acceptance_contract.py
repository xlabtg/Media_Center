from __future__ import annotations

from pathlib import Path
from typing import cast

from contribution_ledger import create_contribution_ledger_app
from fastapi import FastAPI
from fastapi.testclient import TestClient
from wallet import WalletAPIState, create_wallet_app

from libs.shared import ServiceTemplateConfig, encode_hs256_jwt

ROOT = Path(__file__).resolve().parents[1]
TENANT_ID = "tenant-a"
LEDGER_JWT_SECRET = "wallet-issue-60-ledger-secret"
WALLET_JWT_SECRET = "wallet-issue-60-secret"


def test_issue_60_wallet_tracks_mcv_operations_from_distribution_and_payout() -> None:
    ledger_client = TestClient(_ledger_app())
    wallet_app = _wallet_app()
    wallet_client = TestClient(wallet_app)

    member_a = ledger_client.post(
        "/contributions",
        headers=_headers(
            jwt_secret=LEDGER_JWT_SECRET,
            subject="member-a",
            roles=("member_full",),
            idempotency_key="issue-60-contribution-a",
        ),
        json={
            "member_id": "member-a",
            "event_type": "content_creation",
            "source_type": "publication",
            "source_ref": "issue-60-publication-a",
            "platform": "telegram",
            "reach": 100_000,
            "occurred_at": "2026-06-19T07:00:00Z",
            "metadata": {"issue": "60"},
        },
    )
    member_b = ledger_client.post(
        "/contributions",
        headers=_headers(
            jwt_secret=LEDGER_JWT_SECRET,
            subject="member-b",
            roles=("member_full",),
            idempotency_key="issue-60-contribution-b",
        ),
        json={
            "member_id": "member-b",
            "event_type": "publish",
            "source_type": "publication",
            "source_ref": "issue-60-publication-b",
            "platform": "vk",
            "occurred_at": "2026-06-19T07:05:00Z",
        },
    )
    recalculated = ledger_client.post(
        "/weights/recalculate",
        headers=_headers(
            jwt_secret=LEDGER_JWT_SECRET,
            subject="council-1",
            roles=("council",),
            idempotency_key="issue-60-weights",
        ),
        json={"period": "2026-06", "avg_points_council": 100},
    )
    distribution = ledger_client.get(
        "/payout-distribution",
        headers=_headers(
            jwt_secret=LEDGER_JWT_SECRET,
            subject="council-1",
            roles=("council",),
        ),
        params={"period": "2026-06"},
    )

    assert member_a.status_code == 201
    assert member_b.status_code == 201
    assert recalculated.status_code == 200
    assert distribution.status_code == 200
    distribution_body = distribution.json()
    member_a_distribution = next(
        member
        for member in distribution_body["members"]
        if member["member_id"] == "member-a"
    )
    assert member_a_distribution["payout_share"] == 0.625

    credited = wallet_client.post(
        "/wallet/operations",
        headers=_headers(
            jwt_secret=WALLET_JWT_SECRET,
            subject="council-1",
            roles=("council",),
            idempotency_key="issue-60-wallet-credit",
        ),
        json={
            "operation_id": "wallet-issue-60-credit",
            "member_id": "member-a",
            "amount_mcv": "62.50",
            "type": "distribution_credit",
            "ref_type": "payout_distribution",
            "ref_id": distribution_body["distribution_id"],
            "period": distribution_body["period"],
            "distribution_hash": distribution_body["distribution_hash"],
            "payout_share": member_a_distribution["payout_share"],
            "created_at": "2026-06-19T08:00:00Z",
        },
    )
    debited = wallet_client.post(
        "/wallet/operations",
        headers=_headers(
            jwt_secret=WALLET_JWT_SECRET,
            subject="council-1",
            roles=("council",),
            idempotency_key="issue-60-wallet-debit",
        ),
        json={
            "operation_id": "wallet-issue-60-debit",
            "member_id": "member-a",
            "amount_mcv": "-12.50",
            "type": "payout_debit",
            "ref_type": "payout",
            "ref_id": "payout-issue-60",
            "period": "2026-06",
            "created_at": "2026-06-19T09:00:00Z",
        },
    )
    balance = wallet_client.get(
        "/wallet/balance",
        headers=_headers(
            jwt_secret=WALLET_JWT_SECRET,
            subject="member-a",
            roles=("member_full",),
        ),
        params={"member_id": "member-a"},
    )
    operations = wallet_client.get(
        "/wallet/operations",
        headers=_headers(
            jwt_secret=WALLET_JWT_SECRET,
            subject="council-1",
            roles=("council",),
        ),
        params={
            "ref_type": "payout_distribution",
            "ref_id": distribution_body["distribution_id"],
        },
    )

    assert credited.status_code == 201
    assert (
        credited.json()["distribution_hash"] == distribution_body["distribution_hash"]
    )
    assert credited.json()["payout_share"] == member_a_distribution["payout_share"]
    assert debited.status_code == 201
    assert balance.status_code == 200
    assert balance.json()["balance_mcv"] == "50.00"
    assert balance.json()["credited_mcv"] == "62.50"
    assert balance.json()["debited_mcv"] == "12.50"
    assert operations.status_code == 200
    assert [item["operation_id"] for item in operations.json()["items"]] == [
        "wallet-issue-60-credit"
    ]

    state = cast(WalletAPIState, wallet_app.state.wallet_api)
    assert [record.event_type for record in state.audit_log_sink.records] == [
        "wallet.operation_recorded",
        "wallet.operation_recorded",
    ]
    first_event_payload = state.publisher.messages[0].envelope.payload
    assert first_event_payload["operation_id"] == "wallet-issue-60-credit"
    assert first_event_payload["ref_type"] == "payout_distribution"
    assert "member_id" not in first_event_payload
    assert "amount_mcv" not in first_event_payload
    assert "balance_after_mcv" not in first_event_payload


def test_issue_60_wallet_docs_are_marked_implemented() -> None:
    spec = (ROOT / "docs/modules/wallet.md").read_text(encoding="utf-8")
    readme = (ROOT / "services/wallet/README.md").read_text(encoding="utf-8")

    for marker in (
        "реализовано для #60",
        "POST** `/wallet/operations`",
        "wallet.operation_recorded",
        "wallet_operations_0004.py",
        "tenant-isolation контракт #60",
    ):
        assert marker in spec

    for marker in (
        "create_wallet_app",
        "POST /wallet/operations",
        "GET /wallet/balance",
        "InMemoryWalletRepository",
    ):
        assert marker in readme


def _ledger_app() -> FastAPI:
    return create_contribution_ledger_app(
        ServiceTemplateConfig(
            service_name="contribution-ledger",
            version="0.1.0",
            jwt_secret=LEDGER_JWT_SECRET,
            prometheus_enabled=True,
        )
    )


def _wallet_app() -> FastAPI:
    return create_wallet_app(
        ServiceTemplateConfig(
            service_name="wallet",
            version="0.1.0",
            jwt_secret=WALLET_JWT_SECRET,
            prometheus_enabled=True,
        )
    )


def _headers(
    *,
    jwt_secret: str,
    subject: str,
    roles: tuple[str, ...],
    tenant_id: str = TENANT_ID,
    correlation_id: str = "corr-wallet-issue-60",
    idempotency_key: str | None = None,
) -> dict[str, str]:
    token = encode_hs256_jwt(
        {
            "tenant_id": tenant_id,
            "sub": subject,
            "roles": list(roles),
        },
        jwt_secret,
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Tenant-Id": tenant_id,
        "X-Correlation-Id": correlation_id,
    }
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key

    return headers
