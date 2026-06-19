from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from fastapi import FastAPI
from hitl_payout_gateway import (
    InMemoryPayoutQueueRepository,
    PayoutConfirmationManager,
    PayoutQueueManager,
    VetoManager,
)
from policy_manager import InMemoryPolicyRepository, PolicyManager
from web_cabinet import (
    CouncilPanelAuditRecord,
    CouncilPanelPayoutAnnotation,
    InMemoryCouncilPanelRepository,
    create_web_cabinet_app,
)

from libs.shared import ServiceTemplateConfig, encode_hs256_jwt

JWT_SECRET = "council-panel-demo-secret"
TOTP_SECRET = "JBSWY3DPEHPK3PXP"
TENANT_ID = "tenant-a"
COUNCIL_ID = "council-1"
DEMO_TOKEN = encode_hs256_jwt(
    {
        "tenant_id": TENANT_ID,
        "sub": COUNCIL_ID,
        "roles": ["council"],
    },
    JWT_SECRET,
)


def build_demo_app() -> FastAPI:
    payout_repository = InMemoryPayoutQueueRepository()
    payout_queue_manager = PayoutQueueManager(
        repository=payout_repository,
        veto_window_hours=8,
    )
    council_panel_repository = InMemoryCouncilPanelRepository()
    _seed_demo_data(
        payout_queue_manager=payout_queue_manager,
        council_panel_repository=council_panel_repository,
    )
    return create_web_cabinet_app(
        ServiceTemplateConfig(
            service_name="web-cabinet",
            version="0.1.0",
            jwt_secret=JWT_SECRET,
            prometheus_enabled=True,
        ),
        payout_queue_manager=payout_queue_manager,
        veto_manager=VetoManager(repository=payout_repository),
        confirmation_manager=PayoutConfirmationManager(
            repository=payout_repository,
        ),
        policy_manager=PolicyManager(repository=InMemoryPolicyRepository()),
        council_panel_repository=council_panel_repository,
        totp_secrets={(TENANT_ID, COUNCIL_ID): TOTP_SECRET},
    )


def demo_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {DEMO_TOKEN}",
        "X-Tenant-Id": TENANT_ID,
        "X-Correlation-Id": "corr-council-panel-demo",
    }


def _seed_demo_data(
    *,
    payout_queue_manager: PayoutQueueManager,
    council_panel_repository: InMemoryCouncilPanelRepository,
) -> None:
    high_risk_at = datetime(2026, 6, 19, 4, 30, tzinfo=UTC)
    normal_at = datetime(2026, 6, 19, 8, 0, tzinfo=UTC)
    for payout_id, member_id, payout_share, distribution_hash, queued_at in (
        (
            "payout-high-risk",
            "member-high",
            0.45,
            "b" * 64,
            high_risk_at,
        ),
        (
            "payout-normal",
            "member-normal",
            0.12,
            "c" * 64,
            normal_at,
        ),
    ):
        asyncio.run(
            payout_queue_manager.queue_payout(
                tenant_id=TENANT_ID,
                member_id=member_id,
                period="2026-06",
                payout_share=payout_share,
                distribution_id=f"distribution-{payout_id}",
                distribution_hash=distribution_hash,
                created_by=COUNCIL_ID,
                correlation_id="corr-council-panel-demo-seed",
                payout_id=payout_id,
                event_id=f"evt-{payout_id}-queued",
                now=queued_at,
            )
        )

    council_panel_repository.save_annotation(
        CouncilPanelPayoutAnnotation(
            tenant_id=TENANT_ID,
            payout_id="payout-high-risk",
            risk_level="high",
            risk_reason="Новый получатель и высокая доля выплаты",
            policy_key="hitl.veto_window_hours",
            calculation_source="payout_distribution",
            calculation_explanation="Кв ограничен 0.10, доля взята из snapshot",
        )
    )
    council_panel_repository.save_annotation(
        CouncilPanelPayoutAnnotation(
            tenant_id=TENANT_ID,
            payout_id="payout-normal",
            risk_level="medium",
            risk_reason="Плановая выплата",
            policy_key="hitl.veto_window_hours",
            calculation_source="payout_distribution",
            calculation_explanation="Плановый snapshot распределения",
        )
    )
    council_panel_repository.add_audit_record(
        CouncilPanelAuditRecord(
            tenant_id=TENANT_ID,
            payout_id="payout-high-risk",
            event_type="payout.queued",
            event_id="evt-panel-high-queued",
            audit_hash="a" * 64,
            occurred_at=high_risk_at,
        )
    )


app = build_demo_app()
