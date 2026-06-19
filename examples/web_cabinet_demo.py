from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import FastAPI
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
    create_web_cabinet_app,
)

from libs.shared import ServiceTemplateConfig, encode_hs256_jwt

JWT_SECRET = "web-cabinet-demo-secret"
TENANT_ID = "tenant-a"
MEMBER_ID = "member-a"
PERIOD = "2026-06"
DEMO_TOKEN = encode_hs256_jwt(
    {
        "tenant_id": TENANT_ID,
        "sub": MEMBER_ID,
        "roles": ["member_full"],
    },
    JWT_SECRET,
)


def build_demo_app() -> FastAPI:
    wallet_repository = InMemoryWalletRepository()
    cabinet_repository = InMemoryWebCabinetRepository()
    _seed_demo_data(
        wallet_repository=wallet_repository,
        cabinet_repository=cabinet_repository,
    )
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


def demo_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {DEMO_TOKEN}",
        "X-Tenant-Id": TENANT_ID,
        "X-Correlation-Id": "corr-web-cabinet-demo",
    }


def _seed_demo_data(
    *,
    wallet_repository: InMemoryWalletRepository,
    cabinet_repository: InMemoryWebCabinetRepository,
) -> None:
    member_hash = subject_ref_hash(tenant_id=TENANT_ID, subject_id=MEMBER_ID)
    council_hash = subject_ref_hash(tenant_id=TENANT_ID, subject_id="council-1")
    wallet_repository.add_operation(
        WalletOperationRecord(
            operation_id="wallet-op-credit-demo",
            tenant_id=TENANT_ID,
            member_id=MEMBER_ID,
            member_hash=member_hash,
            amount_mcv=Decimal("120.00"),
            balance_after_mcv=Decimal("120.00"),
            type=WalletOperationType.DISTRIBUTION_CREDIT.value,
            ref_type="payout_distribution",
            ref_id="distribution-demo",
            period=PERIOD,
            distribution_hash="a" * 64,
            payout_share=0.625,
            metadata={"source": "demo"},
            audit_hash="b" * 64,
            idempotency_key="wallet-credit-demo",
            request_hash="c" * 64,
            created_by="council-1",
            created_by_hash=council_hash,
            created_at=datetime(2026, 6, 19, 8, 0, tzinfo=UTC),
        )
    )
    wallet_repository.add_operation(
        WalletOperationRecord(
            operation_id="wallet-op-debit-demo",
            tenant_id=TENANT_ID,
            member_id=MEMBER_ID,
            member_hash=member_hash,
            amount_mcv=Decimal("-20.00"),
            balance_after_mcv=Decimal("100.00"),
            type=WalletOperationType.PAYOUT_DEBIT.value,
            ref_type="payout",
            ref_id="payout-demo",
            period=PERIOD,
            distribution_hash=None,
            payout_share=None,
            metadata={},
            audit_hash="d" * 64,
            idempotency_key="wallet-debit-demo",
            request_hash="e" * 64,
            created_by="council-1",
            created_by_hash=council_hash,
            created_at=datetime(2026, 6, 19, 9, 0, tzinfo=UTC),
        )
    )
    cabinet_repository.save_contribution(
        CabinetContributionRecord(
            tenant_id=TENANT_ID,
            member_id=MEMBER_ID,
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
            tenant_id=TENANT_ID,
            owner_id=MEMBER_ID,
            content_id="content-member-a-digest",
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
                    owner_id=MEMBER_ID,
                    url=f"https://authors.example/{MEMBER_ID}",
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


app = build_demo_app()
