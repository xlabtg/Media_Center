from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
from hitl_payout_gateway import (
    InMemoryPayoutQueueRepository,
    PayoutConfirmationManager,
    PayoutQueueManager,
    PayoutStatus,
    VetoManager,
)
from messenger_adapter import (
    InMemoryTelegramMemberContextProvider,
    InMemoryTelegramProxyDirectory,
    TelegramClientExchange,
    TelegramClientGateway,
    TelegramClientScenario,
    TelegramIdentityCipher,
    TelegramInboundMessage,
    TelegramMemberSnapshot,
    TelegramProxyEndpoint,
    TelegramProxyProtocol,
    TelegramProxyRotator,
)
from policy_manager import InMemoryPolicyRepository, PolicyManager
from wallet import (
    InMemoryWalletRepository,
    WalletOperationRecord,
    WalletOperationType,
)
from wallet import (
    subject_ref_hash as wallet_subject_ref_hash,
)
from web_cabinet import (
    CabinetContentRecord,
    CabinetContributionRecord,
    CabinetReferralLink,
    CouncilPanelAuditRecord,
    CouncilPanelPayoutAnnotation,
    InMemoryCouncilPanelRepository,
    InMemoryWebCabinetRepository,
    OnboardingAssistantAnswerRecord,
    OnboardingConsentRecord,
    OnboardingProfileRecord,
    OnboardingStepRecord,
    WebCabinetAPIState,
    create_web_cabinet_app,
)

from libs.shared import ServiceTemplateConfig, encode_hs256_jwt

ROOT = Path(__file__).resolve().parents[1]
JWT_SECRET = "stage4-client-ux-issue-74-secret"
PERIOD = "2026-06"
ANALYTICS_PERIOD = "2026-W25"
NOW = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
TELEGRAM_USER_ID = "10987654321"


def test_issue_74_stage4_client_interfaces_acceptance_flow() -> None:
    app = _app()
    _seed_stage4_projection(app)
    client = TestClient(app)

    cabinet = client.get(
        "/cabinet/overview",
        headers=_headers(subject="member-a", roles=("member_full",)),
        params={"period": PERIOD},
    )
    council_panel = client.get(
        "/council/panel/overview",
        headers=_headers(subject="council-1", roles=("council",)),
        params={"now": NOW.isoformat()},
    )
    policy = client.put(
        "/council/policies/hitl.veto_window_hours",
        headers=_headers(subject="council-1", roles=("council",)),
        json={
            "value": {
                "kind": "threshold",
                "target": "veto_window_hours",
                "operator": "between",
                "min": 6,
                "max": 12,
                "default": 10,
                "reason": "veto_window_out_of_range",
                "decision_on_violation": "escalate",
            },
            "updated_at": "2026-06-20T10:05:00Z",
            "metadata": {"stage": "4", "issue": "74"},
        },
    )
    veto = client.post(
        "/council/payouts/payout-stage4/veto",
        headers=_headers(subject="council-2", roles=("council",)),
        json={
            "decision_id": "veto-stage4",
            "event_id": "evt-stage4-veto",
            "reason_code": "stage4_acceptance",
            "reason": "Совет остановил выплату из клиентской панели",
            "now": "2026-06-20T10:10:00Z",
        },
    )
    onboarding = client.get(
        "/onboarding/overview",
        headers=_headers(subject="candidate-a", roles=("member_assoc",)),
    )
    analytics = client.get(
        "/analytics/dashboard",
        headers=_headers(subject="board-1", roles=("board",)),
        params={"period": ANALYTICS_PERIOD},
    )
    design_system = client.get(
        "/design-system/tokens",
        headers=_headers(subject="board-1", roles=("board",)),
    )
    telegram_exchange = asyncio.run(_telegram_exchange())

    assert cabinet.status_code == 200
    cabinet_body = cabinet.json()
    assert cabinet_body["tenant_id"] == "tenant-a"
    assert cabinet_body["member_id"] == "member-a"
    assert cabinet_body["contribution"]["total_points"] == 135.0
    assert cabinet_body["balance"]["balance_mcv"] == "100.00"
    assert [item["operation_id"] for item in cabinet_body["operations"]] == [
        "wallet-stage4-debit",
        "wallet-stage4-credit",
    ]
    assert cabinet_body["content"][0]["title"] == "Кооперативный дайджест"

    assert council_panel.status_code == 200
    council_body = council_panel.json()
    assert council_body["summary"]["queued"] == 1
    assert council_body["summary"]["requires_2fa"] == 1
    assert council_body["payouts"][0]["payout_id"] == "payout-stage4"
    assert council_body["payouts"][0]["veto_available"] is True
    assert policy.status_code == 200
    assert policy.json()["version"] == 2
    assert policy.json()["updated_by"] == "council-1"
    assert veto.status_code == 200
    assert veto.json()["decision_id"] == "veto-stage4"
    state = cast(WebCabinetAPIState, app.state.web_cabinet_api)
    assert (
        state.payout_queue_manager.get_payout(
            tenant_id="tenant-a",
            payout_id="payout-stage4",
        ).status
        is PayoutStatus.CANCELED
    )

    assert onboarding.status_code == 200
    onboarding_body = onboarding.json()
    assert onboarding_body["target_window_hours"] == 24
    assert onboarding_body["progress_percent"] == 100
    assert onboarding_body["readiness"]["ready_for_review"] is True
    assert onboarding_body["assistant"]["enabled"] is True

    assert analytics.status_code == 200
    assert "Дашборд KPI" in analytics.text
    assert 'data-design-system="nmc-ui"' in analytics.text
    assert design_system.status_code == 200
    components = {item["name"] for item in design_system.json()["components"]}
    assert {"AppShell", "MetricTile", "HITLQueueItem", "ConsentControl"} <= components

    assert telegram_exchange.scenario is TelegramClientScenario.BALANCE
    assert telegram_exchange.proxy_lease is not None
    assert telegram_exchange.proxy_lease.proxy_id == "proxy-http"
    assert "Баллы: 4242" in telegram_exchange.reply.text
    assert TELEGRAM_USER_ID not in telegram_exchange.model_dump_json()


def test_issue_74_stage4_acceptance_snapshot_is_documented() -> None:
    snapshot = (ROOT / "docs/STAGE_4_ACCEPTANCE.md").read_text(encoding="utf-8")
    web_cabinet = (ROOT / "docs/modules/web-cabinet.md").read_text(encoding="utf-8")
    messenger = (ROOT / "docs/modules/messenger-adapter.md").read_text(encoding="utf-8")

    for marker in (
        "Acceptance snapshot этапа 4",
        "issue #74",
        "tests/test_stage4_acceptance_contract.py",
        "Веб-кабинет пайщика",
        "Панель Совета",
        "Дашборд KPI",
        "Онбординг",
        "Telegram-клиент",
        "UI голосового ассистента",
        "Дизайн-система",
    ):
        assert marker in snapshot

    for marker in (
        "сквозной stage-4 acceptance contract",
        "tests/test_stage4_acceptance_contract.py",
    ):
        assert marker in web_cabinet

    assert "stage-4 acceptance contract" in messenger


def _app() -> FastAPI:
    payout_repository = InMemoryPayoutQueueRepository()
    return create_web_cabinet_app(
        ServiceTemplateConfig(
            service_name="web-cabinet",
            version="0.1.0",
            jwt_secret=JWT_SECRET,
            prometheus_enabled=True,
        ),
        repository=InMemoryWebCabinetRepository(),
        wallet_repository=InMemoryWalletRepository(),
        payout_queue_manager=PayoutQueueManager(
            repository=payout_repository,
            veto_window_hours=8,
        ),
        veto_manager=VetoManager(repository=payout_repository),
        confirmation_manager=PayoutConfirmationManager(repository=payout_repository),
        policy_manager=PolicyManager(repository=InMemoryPolicyRepository()),
        council_panel_repository=InMemoryCouncilPanelRepository(),
        totp_secrets={
            ("tenant-a", "council-1"): "JBSWY3DPEHPK3PXP",
            ("tenant-a", "council-2"): "JBSWY3DPEHPK3PXP",
        },
    )


def _headers(
    *,
    tenant_id: str = "tenant-a",
    subject: str,
    roles: tuple[str, ...],
    correlation_id: str = "corr-stage4-issue-74",
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


def _seed_stage4_projection(app: FastAPI) -> None:
    state = cast(WebCabinetAPIState, app.state.web_cabinet_api)
    _seed_member_value(
        wallet_repository=state.wallet_repository,
        cabinet_repository=state.repository,
    )
    _seed_onboarding(repository=state.repository)
    asyncio.run(
        state.payout_queue_manager.queue_payout(
            tenant_id="tenant-a",
            member_id="member-a",
            period=PERIOD,
            payout_share=0.45,
            distribution_id="distribution-stage4",
            distribution_hash="a" * 64,
            created_by="council-1",
            correlation_id="corr-stage4-payout",
            payout_id="payout-stage4",
            event_id="evt-stage4-payout-queued",
            now="2026-06-20T09:00:00Z",
            metadata={"stage": "4", "issue": "74"},
        )
    )
    state.council_panel_repository.save_annotation(
        CouncilPanelPayoutAnnotation(
            tenant_id="tenant-a",
            payout_id="payout-stage4",
            risk_level="high",
            risk_reason="Высокая доля выплаты требует внимания Совета",
            policy_key="hitl.veto_window_hours",
            calculation_source="payout_distribution",
            calculation_explanation="Доля выплаты взята из snapshot этапа 4",
        )
    )
    state.council_panel_repository.add_audit_record(
        CouncilPanelAuditRecord(
            tenant_id="tenant-a",
            payout_id="payout-stage4",
            event_type="payout.queued",
            event_id="evt-stage4-payout-queued",
            audit_hash="b" * 64,
            occurred_at=datetime(2026, 6, 20, 9, 0, tzinfo=UTC),
        )
    )


def _seed_member_value(
    *,
    wallet_repository: InMemoryWalletRepository,
    cabinet_repository: InMemoryWebCabinetRepository,
) -> None:
    member_hash = wallet_subject_ref_hash(
        tenant_id="tenant-a",
        subject_id="member-a",
    )
    created_by_hash = wallet_subject_ref_hash(
        tenant_id="tenant-a",
        subject_id="council-1",
    )
    wallet_repository.add_operation(
        WalletOperationRecord(
            operation_id="wallet-stage4-credit",
            tenant_id="tenant-a",
            member_id="member-a",
            member_hash=member_hash,
            amount_mcv=Decimal("120.00"),
            balance_after_mcv=Decimal("120.00"),
            type=WalletOperationType.DISTRIBUTION_CREDIT.value,
            ref_type="payout_distribution",
            ref_id="distribution-stage4",
            period=PERIOD,
            distribution_hash="c" * 64,
            payout_share=0.625,
            metadata={"stage": "4"},
            audit_hash="d" * 64,
            idempotency_key="wallet-stage4-credit",
            request_hash="e" * 64,
            created_by="council-1",
            created_by_hash=created_by_hash,
            created_at=datetime(2026, 6, 20, 8, 0, tzinfo=UTC),
        )
    )
    wallet_repository.add_operation(
        WalletOperationRecord(
            operation_id="wallet-stage4-debit",
            tenant_id="tenant-a",
            member_id="member-a",
            member_hash=member_hash,
            amount_mcv=Decimal("-20.00"),
            balance_after_mcv=Decimal("100.00"),
            type=WalletOperationType.PAYOUT_DEBIT.value,
            ref_type="payout",
            ref_id="payout-stage4",
            period=PERIOD,
            distribution_hash=None,
            payout_share=None,
            metadata={"stage": "4"},
            audit_hash="f" * 64,
            idempotency_key="wallet-stage4-debit",
            request_hash="1" * 64,
            created_by="council-1",
            created_by_hash=created_by_hash,
            created_at=datetime(2026, 6, 20, 9, 0, tzinfo=UTC),
        )
    )
    cabinet_repository.save_contribution(
        CabinetContributionRecord(
            tenant_id="tenant-a",
            member_id="member-a",
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
            tenant_id="tenant-a",
            owner_id="member-a",
            content_id="content-stage4-digest",
            template_id="template-digest",
            title="Кооперативный дайджест",
            preview="Материал готов к публикации.",
            content_hash="2" * 64,
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
                    owner_id="member-a",
                    url="https://authors.example/member-a",
                    reward_share=0.1,
                ),
            ),
            points_awarded=27.0,
            created_at=datetime(2026, 6, 20, 9, 30, tzinfo=UTC),
        )
    )


def _seed_onboarding(*, repository: InMemoryWebCabinetRepository) -> None:
    repository.save_onboarding_profile(
        OnboardingProfileRecord(
            tenant_id="tenant-a",
            member_id="candidate-a",
            started_at=datetime(2026, 6, 20, 8, 0, tzinfo=UTC),
            target_window_hours=24,
            status_recommendation="member_assoc",
        )
    )
    for step in (
        OnboardingStepRecord(
            tenant_id="tenant-a",
            member_id="candidate-a",
            step_id="profile",
            title="Анкета участника",
            description="Заполнить базовую анкету.",
            order=1,
            required=True,
            status="completed",
            completed_at=datetime(2026, 6, 20, 8, 30, tzinfo=UTC),
        ),
        OnboardingStepRecord(
            tenant_id="tenant-a",
            member_id="candidate-a",
            step_id="channels",
            title="Каналы связи",
            description="Подключить Telegram.",
            order=2,
            required=True,
            status="completed",
            completed_at=datetime(2026, 6, 20, 9, 0, tzinfo=UTC),
        ),
    ):
        repository.save_onboarding_step(step)
    repository.save_onboarding_consent(
        OnboardingConsentRecord(
            tenant_id="tenant-a",
            member_id="candidate-a",
            key="content_rules",
            label="Правила достоверного контента",
            required=True,
            granted=True,
            granted_at=datetime(2026, 6, 20, 8, 45, tzinfo=UTC),
        )
    )
    repository.save_onboarding_assistant_answer(
        OnboardingAssistantAnswerRecord(
            tenant_id="tenant-a",
            question_id="faq-contribution",
            question="Как учитывается вклад?",
            answer="Вклад считается по событиям и формирует Кв.",
            confidence=0.9,
            source_refs=("docs/VISION.md#6",),
            topic_tags=("points", "kv"),
            escalation_available=True,
        )
    )


async def _telegram_exchange() -> TelegramClientExchange:
    member_provider = InMemoryTelegramMemberContextProvider()
    member_provider.save(
        TelegramMemberSnapshot(
            tenant_id="tenant-a",
            member_id="member-a",
            status_label="Действительный участник",
            contribution_weight=0.09,
            points_balance=4242,
            open_task_titles=("Проверить материал",),
        )
    )
    proxy_directory = InMemoryTelegramProxyDirectory()
    proxy_directory.register(
        TelegramProxyRotator(
            tenant_id="tenant-a",
            pool_id="stage4-proxy-pool",
            endpoints=(
                TelegramProxyEndpoint(
                    proxy_id="proxy-http",
                    protocol=TelegramProxyProtocol.HTTP,
                    url="https://proxy-a.example:8443",
                    priority=10,
                ),
            ),
        )
    )
    gateway = TelegramClientGateway(
        identity_cipher=TelegramIdentityCipher(
            base64.b64encode(b"4" * 32).decode("ascii")
        ),
        member_provider=member_provider,
        proxy_directory=proxy_directory,
    )
    await gateway.link_account(
        tenant_id="tenant-a",
        member_id="member-a",
        telegram_user_id=TELEGRAM_USER_ID,
        correlation_id="corr-stage4-telegram-link",
        link_id="tg-stage4-link",
        linked_at="2026-06-20T09:30:00Z",
        event_id="evt-stage4-telegram-link",
    )
    return await gateway.handle_update(
        TelegramInboundMessage(
            tenant_id="tenant-a",
            telegram_user_id=TELEGRAM_USER_ID,
            text="/balance",
            correlation_id="corr-stage4-telegram-balance",
            received_at=datetime(2026, 6, 20, 10, 0, tzinfo=UTC),
        ),
        event_id="evt-stage4-telegram-balance",
    )
