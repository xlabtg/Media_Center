from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from wallet import (
    InMemoryWalletRepository,
    WalletOperationRecord,
    WalletOperationType,
    subject_ref_hash,
)
from web_cabinet import (
    CabinetContentRecord,
    CabinetContributionRecord,
    CabinetReferralLink,
    InMemoryWebCabinetRepository,
    WebCabinetAPIState,
    create_web_cabinet_app,
)

from libs.shared import ServiceTemplateConfig, encode_hs256_jwt

ROOT = Path(__file__).resolve().parents[1]
JWT_SECRET = "web-cabinet-issue-67-secret"
PERIOD = "2026-06"


def test_issue_67_web_cabinet_shows_member_value_with_backend_wallet_data() -> None:
    wallet_repository = InMemoryWalletRepository()
    cabinet_repository = InMemoryWebCabinetRepository()
    _seed_member_projection(
        wallet_repository=wallet_repository,
        cabinet_repository=cabinet_repository,
        tenant_id="tenant-a",
        member_id="member-a",
    )
    _seed_member_projection(
        wallet_repository=wallet_repository,
        cabinet_repository=cabinet_repository,
        tenant_id="tenant-b",
        member_id="member-a",
        balance_after_mcv=Decimal("9999.00"),
    )
    app = _app(
        wallet_repository=wallet_repository,
        cabinet_repository=cabinet_repository,
    )
    client = TestClient(app)

    overview = client.get(
        "/cabinet/overview",
        headers=_headers(subject="member-a", roles=("member_full",)),
        params={"period": PERIOD},
    )
    html = client.get(
        "/cabinet",
        headers=_headers(subject="member-a", roles=("member_full",)),
        params={"period": PERIOD},
    )

    assert overview.status_code == 200
    body = overview.json()
    assert body["tenant_id"] == "tenant-a"
    assert body["member_id"] == "member-a"
    assert body["period"] == PERIOD
    assert body["contribution"] == {
        "member_id": "member-a",
        "period": PERIOD,
        "total_points": 135.0,
        "avg_points_council": 150.0,
        "kv_raw": 0.09,
        "kv_capped": 0.09,
        "payout_share": 0.625,
        "contribution_count": 3,
    }
    assert body["balance"]["balance_mcv"] == "100.00"
    assert body["balance"]["credited_mcv"] == "120.00"
    assert body["balance"]["debited_mcv"] == "20.00"
    assert body["balance"]["operation_count"] == 2
    assert [item["operation_id"] for item in body["operations"]] == [
        "wallet-op-debit-member-a",
        "wallet-op-credit-member-a",
    ]
    assert [item["content_id"] for item in body["content"]] == [
        "content-member-a-digest"
    ]
    assert body["content"][0]["title"] == "Кооперативный дайджест"
    assert body["content"][0]["platform_targets"] == ["telegram", "vk"]
    assert [link["level"] for link in body["referral_links"]] == ["L1", "L2", "L3"]
    assert "9999.00" not in overview.text

    assert html.status_code == 200
    assert html.headers["content-type"].startswith("text/html")
    assert '<meta name="viewport"' in html.text
    assert "@media (max-width: 720px)" in html.text
    assert "100.00 МСЦ" in html.text
    assert "Кооперативный дайджест" in html.text
    assert "https://authors.example/member-a" in html.text
    assert "9999.00" not in html.text


def test_issue_67_web_cabinet_enforces_member_scope_rbac_and_tenant_context() -> None:
    wallet_repository = InMemoryWalletRepository()
    cabinet_repository = InMemoryWebCabinetRepository()
    _seed_member_projection(
        wallet_repository=wallet_repository,
        cabinet_repository=cabinet_repository,
        tenant_id="tenant-a",
        member_id="member-a",
    )
    app = _app(
        wallet_repository=wallet_repository,
        cabinet_repository=cabinet_repository,
    )
    client = TestClient(app)

    forbidden = client.get(
        "/cabinet/overview",
        headers=_headers(subject="member-b", roles=("member_full",)),
        params={"member_id": "member-a", "period": PERIOD},
    )
    council_read = client.get(
        "/cabinet/overview",
        headers=_headers(subject="council-1", roles=("council",)),
        params={"member_id": "member-a", "period": PERIOD},
    )
    headers = _headers(subject="member-a", roles=("member_full",))
    headers["X-Tenant-Id"] = "tenant-b"
    tenant_override = client.get(
        "/cabinet/overview",
        headers=headers,
        params={"period": PERIOD},
    )

    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "forbidden"
    assert council_read.status_code == 200
    assert council_read.json()["member_id"] == "member-a"
    assert tenant_override.status_code == 403
    assert tenant_override.json()["error"]["code"] == "tenant_isolation_violation"
    state = app.state.web_cabinet_api
    assert isinstance(state, WebCabinetAPIState)
    assert state.tenant_audit_sink.events[0].event_type == "tenant.isolation_violation"
    assert state.tenant_audit_sink.events[0].tenant_id == "tenant-a"


def test_issue_67_web_cabinet_docs_are_marked_implemented() -> None:
    spec = (ROOT / "docs/modules/web-cabinet.md").read_text(encoding="utf-8")
    readme = (ROOT / "services/web-cabinet/README.md").read_text(encoding="utf-8")

    for marker in (
        "реализовано для #67",
        "GET** `/cabinet/overview`",
        "GET** `/cabinet`",
        "tenant-isolation контракт #67",
        "адаптивный HTML-интерфейс",
    ):
        assert marker in spec

    for marker in (
        "create_web_cabinet_app",
        "GET /cabinet/overview",
        "GET /cabinet",
        "InMemoryWebCabinetRepository",
    ):
        assert marker in readme


def _app(
    *,
    wallet_repository: InMemoryWalletRepository,
    cabinet_repository: InMemoryWebCabinetRepository,
) -> FastAPI:
    return create_web_cabinet_app(
        ServiceTemplateConfig(
            service_name="web-cabinet",
            version="0.1.0",
            jwt_secret=JWT_SECRET,
            prometheus_enabled=True,
        ),
        wallet_repository=wallet_repository,
        repository=cabinet_repository,
    )


def _headers(
    *,
    tenant_id: str = "tenant-a",
    subject: str,
    roles: tuple[str, ...],
    correlation_id: str = "corr-web-cabinet-issue-67",
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
        "X-Correlation-Id": correlation_id,
    }


def _seed_member_projection(
    *,
    wallet_repository: InMemoryWalletRepository,
    cabinet_repository: InMemoryWebCabinetRepository,
    tenant_id: str,
    member_id: str,
    balance_after_mcv: Decimal = Decimal("100.00"),
) -> None:
    member_hash = subject_ref_hash(tenant_id=tenant_id, subject_id=member_id)
    wallet_repository.add_operation(
        WalletOperationRecord(
            operation_id=f"wallet-op-credit-{member_id}",
            tenant_id=tenant_id,
            member_id=member_id,
            member_hash=member_hash,
            amount_mcv=Decimal("120.00"),
            balance_after_mcv=Decimal("120.00"),
            type=WalletOperationType.DISTRIBUTION_CREDIT.value,
            ref_type="payout_distribution",
            ref_id=f"distribution-{member_id}",
            period=PERIOD,
            distribution_hash="a" * 64,
            payout_share=0.625,
            metadata={"source": "issue-67"},
            audit_hash="b" * 64,
            idempotency_key=f"wallet-credit-{tenant_id}-{member_id}",
            request_hash="c" * 64,
            created_by="council-1",
            created_by_hash=subject_ref_hash(
                tenant_id=tenant_id,
                subject_id="council-1",
            ),
            created_at=datetime(2026, 6, 19, 8, 0, tzinfo=UTC),
        )
    )
    wallet_repository.add_operation(
        WalletOperationRecord(
            operation_id=f"wallet-op-debit-{member_id}",
            tenant_id=tenant_id,
            member_id=member_id,
            member_hash=member_hash,
            amount_mcv=Decimal("-20.00"),
            balance_after_mcv=balance_after_mcv,
            type=WalletOperationType.PAYOUT_DEBIT.value,
            ref_type="payout",
            ref_id=f"payout-{member_id}",
            period=PERIOD,
            distribution_hash=None,
            payout_share=None,
            metadata={},
            audit_hash="d" * 64,
            idempotency_key=f"wallet-debit-{tenant_id}-{member_id}",
            request_hash="e" * 64,
            created_by="council-1",
            created_by_hash=subject_ref_hash(
                tenant_id=tenant_id,
                subject_id="council-1",
            ),
            created_at=datetime(2026, 6, 19, 9, 0, tzinfo=UTC),
        )
    )
    cabinet_repository.save_contribution(
        CabinetContributionRecord(
            tenant_id=tenant_id,
            member_id=member_id,
            period=PERIOD,
            total_points=135.0,
            avg_points_council=150.0,
            kv_raw=0.09,
            kv_capped=0.09,
            payout_share=0.625,
            contribution_count=3,
        )
    )
    cabinet_repository.add_content(
        CabinetContentRecord(
            tenant_id=tenant_id,
            owner_id=member_id,
            content_id=f"content-{member_id}-digest",
            template_id="template-digest",
            title="Кооперативный дайджест",
            preview="Материал готов к публикации.",
            content_hash="f" * 64,
            platform_targets=("telegram", "vk"),
            referral_links=(
                CabinetReferralLink(
                    level="L1",
                    owner_id="admin-main",
                    url="https://nmc.example/join?ref=admin-main",
                    reward_share=0.2,
                ),
                CabinetReferralLink(
                    level="L2",
                    owner_id=member_id,
                    url=f"https://authors.example/{member_id}",
                    reward_share=0.1,
                ),
                CabinetReferralLink(
                    level="L3",
                    owner_id="partner-a",
                    url="https://partners.example/a",
                    reward_share=0.05,
                ),
            ),
            points_awarded=27.0,
            created_at=datetime(2026, 6, 19, 10, 0, tzinfo=UTC),
        )
    )
