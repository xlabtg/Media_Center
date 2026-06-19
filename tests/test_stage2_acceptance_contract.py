from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import cast

from blockchain_auditor import (
    BlockchainAuditorAPIState,
    InMemoryGrpcBlockchainAuditTransport,
    create_blockchain_auditor_app,
)
from cglr import CGLRAPIState, create_cglr_app
from contribution_ledger import (
    ContributionLedgerAPIState,
    create_contribution_ledger_app,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hitl_payout_gateway import HITLPayoutAPIState, create_hitl_payout_app
from messenger_adapter import (
    BasePlatformAdapter,
    InMemoryPlatformPublisher,
    InMemoryPlatformRegistry,
    InMemoryPlatformTokenStore,
    PlatformContentLimits,
    PlatformRegistryEntry,
    PlatformStatus,
    PlatformTokenCipher,
    PublicationBatchRequest,
    PublicationBatchResult,
    UnifiedMessengerAdapter,
)

from libs.shared import ServiceTemplateConfig, encode_hs256_jwt

ROOT = Path(__file__).resolve().parents[1]
TENANT_ID = "tenant-a"
LEDGER_SIGNING_VALUE = "a"
CGLR_SIGNING_VALUE = "b"
HITL_SIGNING_VALUE = "c"
AUDITOR_SIGNING_VALUE = "d"


def test_issue_53_contribution_to_vetoed_payout_and_audit_chain_flow() -> None:
    ledger_app = _contribution_ledger_app()
    ledger_client = TestClient(ledger_app)

    member_a = ledger_client.post(
        "/contributions",
        headers=_headers(
            jwt_value=LEDGER_SIGNING_VALUE,
            subject="member-a",
            roles=("member_full",),
            correlation_id="corr-stage2-ledger",
            idempotency_id="contribution-a",
        ),
        json={
            "member_id": "member-a",
            "event_type": "content_creation",
            "source_type": "publication",
            "source_ref": "stage2-publication-a",
            "platform": "telegram",
            "reach": 100_000,
            "occurred_at": "2026-06-18T12:00:00Z",
            "metadata": {"stage": "2"},
        },
    )
    member_b = ledger_client.post(
        "/contributions",
        headers=_headers(
            jwt_value=LEDGER_SIGNING_VALUE,
            subject="member-b",
            roles=("member_full",),
            correlation_id="corr-stage2-ledger",
            idempotency_id="contribution-b",
        ),
        json={
            "member_id": "member-b",
            "event_type": "publish",
            "source_type": "publication",
            "source_ref": "stage2-publication-b",
            "platform": "vk",
            "occurred_at": "2026-06-18T12:05:00Z",
            "metadata": {"stage": "2"},
        },
    )
    recalculated = ledger_client.post(
        "/weights/recalculate",
        headers=_headers(
            jwt_value=LEDGER_SIGNING_VALUE,
            subject="council-1",
            roles=("council",),
            correlation_id="corr-stage2-ledger",
            idempotency_id="weights-2026-06",
        ),
        json={"period": "2026-06", "avg_points_council": 100},
    )
    distribution = ledger_client.get(
        "/payout-distribution",
        headers=_headers(
            jwt_value=LEDGER_SIGNING_VALUE,
            subject="council-1",
            roles=("council",),
            correlation_id="corr-stage2-ledger",
        ),
        params={"period": "2026-06"},
    )

    assert member_a.status_code == 201
    assert member_b.status_code == 201
    assert member_a.json()["points_awarded"] == 27.0
    assert member_b.json()["points_awarded"] == 6.0
    assert recalculated.status_code == 200
    assert recalculated.json()["total_kv_capped"] == 0.16
    assert [member["payout_share"] for member in recalculated.json()["members"]] == [
        0.625,
        0.375,
    ]
    assert distribution.status_code == 200

    distribution_body = distribution.json()
    hitl_app = _hitl_payout_app()
    hitl_client = TestClient(hitl_app)
    queued = hitl_client.post(
        "/payouts/queue",
        headers=_headers(
            jwt_value=HITL_SIGNING_VALUE,
            subject="council-1",
            roles=("council",),
            correlation_id="corr-stage2-hitl",
        ),
        json={
            "payout_id": "payout-stage2-veto",
            "event_id": "evt-stage2-payout-queued",
            "member_id": "member-a",
            "period": distribution_body["period"],
            "payout_share": distribution_body["members"][0]["payout_share"],
            "distribution_id": distribution_body["distribution_id"],
            "distribution_hash": distribution_body["distribution_hash"],
            "created_by": "council-1",
            "now": "2026-06-18T12:00:00Z",
            "metadata": {"stage": "2", "source": "stage2_acceptance"},
        },
    )
    vetoed = hitl_client.post(
        "/payouts/payout-stage2-veto/veto",
        headers=_headers(
            jwt_value=HITL_SIGNING_VALUE,
            subject="council-2",
            roles=("council",),
            correlation_id="corr-stage2-hitl",
        ),
        json={
            "decision_id": "veto-stage2",
            "event_id": "evt-stage2-payout-vetoed",
            "reason_code": "acceptance_check",
            "reason": "Совет остановил тестовую выплату в окне вето",
            "now": "2026-06-18T14:00:00Z",
            "metadata": {"stage": "2"},
        },
    )
    executed_after_veto = hitl_client.post(
        "/payouts/payout-stage2-veto/execute",
        headers=_headers(
            jwt_value=HITL_SIGNING_VALUE,
            subject="council-2",
            roles=("council",),
            correlation_id="corr-stage2-hitl",
        ),
        json={"now": "2026-06-18T20:01:00Z"},
    )
    canceled = hitl_client.get(
        "/payouts",
        headers=_headers(
            jwt_value=HITL_SIGNING_VALUE,
            subject="council-1",
            roles=("council",),
            correlation_id="corr-stage2-hitl",
        ),
        params={"status": "canceled"},
    )

    assert queued.status_code == 201
    assert queued.json()["status"] == "queued"
    assert queued.json()["requires_2fa"] is True
    assert queued.json()["veto_until"] == "2026-06-18T20:00:00Z"
    assert vetoed.status_code == 200
    assert vetoed.json()["decision_id"] == "veto-stage2"
    assert executed_after_veto.status_code == 409
    assert executed_after_veto.json()["error"]["code"] == "payout_not_executable"
    assert canceled.status_code == 200
    assert [item["payout_id"] for item in canceled.json()["items"]] == [
        "payout-stage2-veto"
    ]

    ledger_state = cast(
        ContributionLedgerAPIState,
        ledger_app.state.contribution_ledger_api,
    )
    hitl_state = cast(HITLPayoutAPIState, hitl_app.state.hitl_payout_api)
    assert [record.event_type for record in hitl_state.audit_log_sink.records] == [
        "payout.queued",
        "payout.vetoed",
    ]

    first_contribution_event = ledger_state.publisher.messages[0].envelope
    auditor_app = _blockchain_auditor_app()
    auditor_client = TestClient(auditor_app)
    audit_recorded = auditor_client.post(
        "/audit/record",
        headers=_headers(
            jwt_value=AUDITOR_SIGNING_VALUE,
            subject="council-1",
            roles=("council",),
            correlation_id="corr-stage2-audit",
        ),
        json={
            "records": [
                {
                    "event_id": first_contribution_event.event_id,
                    "event_type": first_contribution_event.type,
                    "audit_hash": member_a.json()["audit_hash"],
                    "occurred_at": "2026-06-18T12:00:00Z",
                    "metadata": {
                        "source": "contribution-ledger",
                        "schema_version": "1.0",
                    },
                },
                {
                    "event_id": "evt-stage2-payout-queued",
                    "event_type": "payout.queued",
                    "audit_hash": queued.json()["audit_hash"],
                    "occurred_at": "2026-06-18T12:00:00Z",
                    "metadata": {
                        "source": "hitl-payout-gateway",
                        "payout_id": "payout-stage2-veto",
                        "status": "queued",
                    },
                },
                {
                    "event_id": "evt-stage2-payout-vetoed",
                    "event_type": "payout.vetoed",
                    "audit_hash": vetoed.json()["audit_hash"],
                    "occurred_at": "2026-06-18T14:00:00Z",
                    "metadata": {
                        "source": "hitl-payout-gateway",
                        "payout_id": "payout-stage2-veto",
                        "reason_code": "acceptance_check",
                        "status": "canceled",
                    },
                },
            ]
        },
    )
    fetched_audit = auditor_client.get(
        "/audit/records/evt-stage2-payout-vetoed",
        headers=_headers(
            jwt_value=AUDITOR_SIGNING_VALUE,
            subject="council-1",
            roles=("council",),
            correlation_id="corr-stage2-audit",
        ),
    )

    assert audit_recorded.status_code == 201
    assert [item["event_id"] for item in audit_recorded.json()["items"]] == [
        first_contribution_event.event_id,
        "evt-stage2-payout-queued",
        "evt-stage2-payout-vetoed",
    ]
    assert fetched_audit.status_code == 200
    assert fetched_audit.json()["audit_hash"] == vetoed.json()["audit_hash"]

    auditor_state = cast(
        BlockchainAuditorAPIState,
        auditor_app.state.blockchain_auditor_api,
    )
    transport = cast(
        InMemoryGrpcBlockchainAuditTransport,
        auditor_state.connector.transport,
    )
    recorded_batch = transport.batch_record_requests[0]
    assert len(recorded_batch) == 3
    assert all("amount" not in command.metadata for command in recorded_batch)
    assert all("member_id" not in command.metadata for command in recorded_batch)
    assert all("payout_share" not in command.metadata for command in recorded_batch)


def test_issue_53_generation_to_publication_flow() -> None:
    cglr_app = _cglr_app()
    cglr_client = TestClient(cglr_app)

    generated = cglr_client.post(
        "/generate",
        headers=_headers(
            jwt_value=CGLR_SIGNING_VALUE,
            subject="author-7",
            roles=("member_full",),
            correlation_id="corr-stage2-cglr",
            idempotency_id="generation",
        ),
        json={
            "template_id": "stage2-template",
            "template_body": "# {{ title }}\n\n{{ body }}",
            "context": {
                "title": "Дайджест НМЦ",
                "body": "Материал подготовлен к публикации.",
            },
            "validation": {
                "max_length": 500,
                "required_blocks": ["# Дайджест НМЦ"],
            },
            "platform_targets": ["telegram", "vk"],
            "link_routing": {
                "admin_link": {
                    "owner_id": "admin-main",
                    "url": "https://nmc.example/join",
                },
                "author_link": {
                    "owner_id": "author-7",
                    "url": "https://authors.example/author-7",
                },
                "l3_candidates": [
                    {
                        "owner_id": "partner-a",
                        "url": "https://partners.example/a",
                        "contribution_weight": 15,
                    }
                ],
                "rotation_seed": "stage2-acceptance",
            },
            "contribution": {
                "event_type": "content_creation",
                "platform": "telegram",
                "reach": 50_000,
                "metadata": {"stage": "2"},
            },
        },
    )

    assert generated.status_code == 201
    generated_body = generated.json()
    assert generated_body["contribution"]["points_awarded"] == 23.4
    assert "Реферальные ссылки:" in generated_body["content_with_links"]

    publication_result, telegram_publisher, vk_publisher = asyncio.run(
        _publish_generated_content(
            publication_id=generated_body["content_id"],
            content=generated_body["content_with_links"],
        )
    )

    assert publication_result.succeeded_platforms == ("telegram", "vk")
    assert publication_result.failed == ()
    assert (
        telegram_publisher.commands[0].content == generated_body["content_with_links"]
    )
    assert vk_publisher.commands[0].content == generated_body["content_with_links"]
    assert telegram_publisher.commands[0].target_id == "@nmc_channel"
    assert vk_publisher.commands[0].target_id == "-12345"
    assert "tg-stage2-token" not in telegram_publisher.commands[0].model_dump_json()
    assert "vk-stage2-token" not in vk_publisher.commands[0].model_dump_json()

    state = cast(CGLRAPIState, cglr_app.state.cglr_api)
    assert [message.envelope.type for message in state.publisher.messages] == [
        "content.generated",
        "contribution.recorded",
        "audit.record.requested",
    ]


def test_issue_53_stage2_acceptance_snapshot_covers_epic_criteria() -> None:
    acceptance = _read_text("docs/STAGE_2_ACCEPTANCE.md")
    readme = _read_text("README.md")

    for marker in (
        "Статус: acceptance snapshot для issue #53",
        "## 1. Решение по этапу 2",
        "## 2. Трассировка эпиков #34, #38, #43, #48, #52",
        "## 3. Критерии завершения эпика #53",
        "Сценарий «учёт вклада → выплата с вето» проходит end-to-end",
        "Сценарий «генерация → публикация» проходит end-to-end",
        "Аудит-хэши ключевых событий пишутся в блокчейн",
        "Ключевая бизнес-логика покрыта тестами",
        "## 4. Gate перед этапом 3",
        "pytest tests/test_stage2_acceptance_contract.py",
    ):
        assert marker in acceptance

    for issue in (34, 38, 43, 48, 52, 53):
        assert f"#{issue}" in acceptance

    for marker in (
        "services/contribution-ledger/contribution_ledger/api.py",
        "services/cglr/cglr/api.py",
        "services/hitl-payout-gateway/hitl_payout_gateway/api.py",
        "services/messenger-adapter/messenger_adapter/unified_adapter.py",
        "services/blockchain-auditor/blockchain_auditor/api.py",
        "tests/test_stage2_acceptance_contract.py",
    ):
        assert marker in acceptance

    assert "docs/STAGE_2_ACCEPTANCE.md" in readme


def _contribution_ledger_app() -> FastAPI:
    return create_contribution_ledger_app(
        ServiceTemplateConfig(
            service_name="contribution-ledger",
            version="0.1.0",
            jwt_secret=LEDGER_SIGNING_VALUE,
            prometheus_enabled=True,
        )
    )


def _cglr_app() -> FastAPI:
    return create_cglr_app(
        ServiceTemplateConfig(
            service_name="cglr",
            version="0.1.0",
            jwt_secret=CGLR_SIGNING_VALUE,
            prometheus_enabled=True,
        )
    )


def _hitl_payout_app() -> FastAPI:
    return create_hitl_payout_app(
        ServiceTemplateConfig(
            service_name="hitl-payout-gateway",
            version="0.1.0",
            jwt_secret=HITL_SIGNING_VALUE,
            prometheus_enabled=True,
        ),
        veto_window_hours=8,
    )


def _blockchain_auditor_app() -> FastAPI:
    return create_blockchain_auditor_app(
        ServiceTemplateConfig(
            service_name="blockchain-auditor",
            version="0.1.0",
            jwt_secret=AUDITOR_SIGNING_VALUE,
            prometheus_enabled=True,
        ),
        transport=InMemoryGrpcBlockchainAuditTransport(),
    )


async def _publish_generated_content(
    *,
    publication_id: str,
    content: str,
) -> tuple[
    PublicationBatchResult,
    InMemoryPlatformPublisher,
    InMemoryPlatformPublisher,
]:
    registry = InMemoryPlatformRegistry(
        entries=[
            PlatformRegistryEntry(
                tenant_id=TENANT_ID,
                platform="telegram",
                status=PlatformStatus.ACTIVE,
                priority=10,
                limits=PlatformContentLimits(max_text_length=2_000, max_media_items=1),
                parameters={"default_target_id": "@nmc_channel"},
            ),
            PlatformRegistryEntry(
                tenant_id=TENANT_ID,
                platform="vk",
                status=PlatformStatus.ACTIVE,
                priority=20,
                limits=PlatformContentLimits(max_text_length=2_000, max_media_items=1),
                parameters={"default_target_id": "-12345"},
            ),
        ]
    )
    token_store = InMemoryPlatformTokenStore(PlatformTokenCipher(_encryption_key()))
    token_store.save_token(
        tenant_id=TENANT_ID,
        platform="telegram",
        token="tg-stage2-token",
    )
    token_store.save_token(
        tenant_id=TENANT_ID,
        platform="vk",
        token="vk-stage2-token",
    )
    telegram_publisher = InMemoryPlatformPublisher(connector_name="telegram_mock")
    vk_publisher = InMemoryPlatformPublisher(connector_name="vk_mock")
    adapter = UnifiedMessengerAdapter(
        platform_adapters={
            "telegram": BasePlatformAdapter(
                platform="telegram",
                publisher=telegram_publisher,
                token_store=token_store,
                platform_registry=registry,
                sleeper=lambda delay: None,
            ),
            "vk": BasePlatformAdapter(
                platform="vk",
                publisher=vk_publisher,
                token_store=token_store,
                platform_registry=registry,
                sleeper=lambda delay: None,
            ),
        },
        platform_registry=registry,
    )

    result = await adapter.publish(
        PublicationBatchRequest(
            tenant_id=TENANT_ID,
            publication_id=publication_id,
            content=content,
            correlation_id="corr-stage2-publication",
        )
    )
    return result, telegram_publisher, vk_publisher


def _headers(
    *,
    jwt_value: str,
    subject: str,
    roles: tuple[str, ...],
    correlation_id: str,
    tenant_id: str = TENANT_ID,
    idempotency_id: str | None = None,
) -> dict[str, str]:
    token = encode_hs256_jwt(
        {
            "tenant_id": tenant_id,
            "sub": subject,
            "roles": list(roles),
        },
        jwt_value,
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Tenant-Id": tenant_id,
        "X-Correlation-Id": correlation_id,
    }
    if idempotency_id is not None:
        headers["Idempotency-Key"] = idempotency_id

    return headers


def _encryption_key() -> str:
    return base64.b64encode(b"5" * 32).decode("ascii")


def _read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")
