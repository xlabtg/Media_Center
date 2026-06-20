from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs

import httpx
from hitl_payout_gateway import (
    PAYOUT_CONFIRM_OPERATION,
    PAYOUT_PAYMENT_STATUS_SYNCED_EVENT,
    InMemoryBlockchainAuditConnector,
    PayoutConfirmationManager,
    PayoutExecutionManager,
    PayoutPaymentStatus,
    PayoutPaymentStatusReceipt,
    PayoutQueueItem,
    PayoutQueueManager,
    RFPayoutGatewayConfig,
    RFPayoutGatewayConnector,
)
from messenger_adapter import (
    DEFAULT_PLATFORM_CATALOG_SIZE,
    BasePlatformAdapter,
    DzenPostPublisher,
    FallbackChannelPublisher,
    FallbackChannelRoute,
    FallbackChannelStatus,
    FallbackChannelType,
    InMemoryFallbackChannelRegistry,
    InMemoryPlatformRegistry,
    InMemoryPlatformTokenStore,
    InMemoryProxyLeaseProvider,
    OKMediatopicPublisher,
    PlatformContentLimits,
    PlatformPublicationError,
    PlatformPublishCommand,
    PlatformPublishResult,
    PlatformRegistryEntry,
    PlatformStatus,
    PlatformTokenCipher,
    PublicationBatchRequest,
    PublicationBatchResult,
    ResiliencePolicy,
    ResilientPlatformPublisher,
    RetryPolicy,
    UnifiedMessengerAdapter,
    VKAPIRateLimit,
    VKAPIRateLimiter,
    VKWallPublisher,
    default_platform_registry_entries,
)
from pydantic import SecretStr

from libs.shared import (
    AuditLogger,
    InMemoryAuditLogSink,
    InMemoryEventBus,
    TenantContext,
    TOTPService,
)

ROOT = Path(__file__).resolve().parents[1]
TENANT_ID = "tenant-a"
TOTP_SECRET = "JBSWY3DPEHPK3PXP"
PAYOUT_ID = "payout-stage5"


def _encryption_key() -> str:
    return base64.b64encode(b"stage-5-integrations-key-32-bytes!"[:32]).decode("ascii")


def test_issue_82_stage5_integrations_acceptance_flow() -> None:
    asyncio.run(_run_stage5_acceptance_flow())


async def _run_stage5_acceptance_flow() -> None:
    publication, publication_state = await _publish_to_external_platforms()
    payout, payout_state = await _execute_rf_gateway_payout()

    assert publication.succeeded_platforms == ("telegram", "vk", "dzen", "ok")
    assert publication.failed == ()
    assert len(publication.receipts) >= 3
    assert publication_state.proxy_provider.leased_proxy_ids == ("tg-proxy-primary",)
    resilience_metadata = publication_state.telegram_primary.commands[0].metadata[
        "resilience"
    ]
    assert isinstance(resilience_metadata, dict)
    proxy_metadata = resilience_metadata["proxy"]
    assert isinstance(proxy_metadata, dict)
    assert proxy_metadata["proxy_id"] == "tg-proxy-primary"
    assert publication_state.telegram_fallback.attempted_channel_ids == [
        "ipfs-outbox",
        "ton-outbox",
    ]
    assert publication_state.fallback_registry.snapshot(
        tenant_id=TENANT_ID,
        platform="telegram",
    ).channel_statuses == {
        "ipfs-outbox": FallbackChannelStatus.UNHEALTHY,
        "ton-outbox": FallbackChannelStatus.HEALTHY,
    }

    assert payout.status is PayoutPaymentStatus.SUCCEEDED
    assert payout_state.payout.payment_connector_name == "rf_payment_gateway"
    assert payout_state.payout.payment_gateway_id == "rfpay-stage5"
    assert payout_state.blockchain_auditor.records[0].event_type == "payout.executed"
    assert payout_state.blockchain_auditor.records[0].metadata == {
        "payout_id": PAYOUT_ID,
        "execution_id": "execution-stage5",
        "execution_ref_hash": payout_state.payout.execution_ref_hash,
        "source": "hitl-payout-gateway",
    }
    assert [message.envelope.type for message in payout_state.bus.messages] == [
        "payout.queued",
        "payout.confirmed",
        "payout.executed",
        PAYOUT_PAYMENT_STATUS_SYNCED_EVENT,
    ]
    _assert_sensitive_values_absent(
        publication=publication,
        payout_state=payout_state,
    )
    _assert_private_blockchain_network_declared()


def test_issue_82_stage5_acceptance_snapshot_is_documented() -> None:
    snapshot = (ROOT / "docs/STAGE_5_ACCEPTANCE.md").read_text(encoding="utf-8")
    messenger = (ROOT / "docs/modules/messenger-adapter.md").read_text(encoding="utf-8")
    hitl = (ROOT / "docs/modules/hitl-payout-gateway.md").read_text(encoding="utf-8")
    blockchain = (ROOT / "docs/modules/blockchain-auditor.md").read_text(
        encoding="utf-8"
    )

    for marker in (
        "Acceptance snapshot этапа 5",
        "issue #82",
        "tests/test_stage5_acceptance_contract.py",
        "Telegram через Telethon",
        "VK API",
        "Dzen/OK",
        "РФ-платёжный шлюз",
        "приватная блокчейн-сеть",
        "102 площадки",
        "IPFS/TON/Matrix",
    ):
        assert marker in snapshot

    assert "#75" in messenger and "#81" in messenger
    assert "#78" in hitl
    assert "#79" in blockchain


@dataclass(slots=True)
class Stage5PublicationState:
    proxy_provider: InMemoryProxyLeaseProvider
    fallback_registry: InMemoryFallbackChannelRegistry
    telegram_primary: ScriptedPublisher
    telegram_fallback: ScriptedFallbackPublisher


@dataclass(slots=True)
class Stage5PayoutState:
    bus: InMemoryEventBus
    audit_sink: InMemoryAuditLogSink
    blockchain_auditor: InMemoryBlockchainAuditConnector
    payout: PayoutQueueItem


async def _publish_to_external_platforms() -> tuple[
    PublicationBatchResult,
    Stage5PublicationState,
]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "api.vk.com":
            payload = parse_qs(request.content.decode("utf-8"))
            assert request.url.path == "/method/wall.post"
            assert payload["owner_id"] == ["-12345"]
            assert payload["access_token"] == ["vk-stage5-token"]
            return httpx.Response(
                200,
                json={"response": {"post_id": 82, "date": 1781971200}},
            )

        if request.url.host == "dzen.example":
            payload = json.loads(request.content.decode("utf-8"))
            assert request.url.path == "/api/publications"
            assert request.headers["Authorization"] == "OAuth dzen-stage5-token"
            assert payload["channel_id"] == "dzen-channel-stage5"
            assert payload["title"] == "Дайджест интеграций"
            return httpx.Response(
                200,
                json={
                    "result": {
                        "publication_id": "dzen-stage5",
                        "published_at": "2026-06-20T12:00:00Z",
                    }
                },
            )

        if request.url.host == "ok.example":
            payload = parse_qs(request.content.decode("utf-8"))
            attachment = json.loads(payload["attachment"][0])
            assert request.url.path == "/fb.do"
            assert payload["method"] == ["mediatopic.post"]
            assert payload["access_token"] == ["ok-stage5-token"]
            assert payload["gid"] == ["ok-group-stage5"]
            assert attachment["media"][0]["text"].startswith("Готовый материал")
            return httpx.Response(200, json={"topic_id": "ok-stage5"})

        return httpx.Response(404, json={"error": "unexpected request"})

    registry = InMemoryPlatformRegistry(
        entries=[
            _platform_entry("telegram", priority=10, target_id="@nmc_channel"),
            _platform_entry("vk", priority=20, target_id="-12345"),
            _platform_entry("dzen", priority=30, target_id="dzen-channel-stage5"),
            _platform_entry("ok", priority=40, target_id="ok-group-stage5"),
        ]
    )
    token_store = InMemoryPlatformTokenStore(PlatformTokenCipher(_encryption_key()))
    for platform in ("telegram", "vk", "dzen", "ok"):
        token_store.save_token(
            tenant_id=TENANT_ID,
            platform=platform,
            token=f"{platform}-stage5-token",
        )

    proxy_provider = InMemoryProxyLeaseProvider()
    proxy_provider.register_pool(
        tenant_id=TENANT_ID,
        platform="telegram",
        proxies=[
            {
                "proxy_id": "tg-proxy-primary",
                "protocol": "http",
                "url": "https://proxy-stage5.example:8443",
                "secret_ref": "vault://tenant-a/proxies/tg-proxy-primary",
                "priority": 10,
            }
        ],
    )
    fallback_registry = InMemoryFallbackChannelRegistry(
        routes=[
            FallbackChannelRoute(
                tenant_id=TENANT_ID,
                platform="telegram",
                channel_type=FallbackChannelType.IPFS,
                channel_id="ipfs-outbox",
                priority=10,
                endpoint="ipfs://tenant-a/media-center/stage5",
                secret_ref="vault://tenant-a/fallback/ipfs",
            ),
            FallbackChannelRoute(
                tenant_id=TENANT_ID,
                platform="telegram",
                channel_type=FallbackChannelType.TON,
                channel_id="ton-outbox",
                priority=20,
                endpoint="ton://tenant-a/media-center/stage5",
                secret_ref="vault://tenant-a/fallback/ton",
            ),
        ]
    )
    telegram_primary = ScriptedPublisher(
        outcomes=[
            PlatformPublicationError(
                "primary Telegram route is unavailable",
                platform="telegram",
                error_code="platform_unavailable",
                retryable=True,
            )
        ]
    )
    telegram_fallback = ScriptedFallbackPublisher(failing_channels={"ipfs-outbox"})
    telegram_publisher = ResilientPlatformPublisher(
        primary=telegram_primary,
        proxy_leases=proxy_provider,
        fallback_routes=fallback_registry,
        fallback_publisher=telegram_fallback,
        policy=ResiliencePolicy(proxy_pool_required=True),
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await UnifiedMessengerAdapter(
            platform_registry=registry,
            platform_adapters={
                "telegram": BasePlatformAdapter(
                    platform="telegram",
                    publisher=telegram_publisher,
                    token_store=token_store,
                    platform_registry=registry,
                    retry_policy=RetryPolicy(max_attempts=1),
                    sleeper=lambda delay: None,
                ),
                "vk": BasePlatformAdapter(
                    platform="vk",
                    publisher=VKWallPublisher(
                        client=client,
                        rate_limiter=VKAPIRateLimiter(
                            limit=VKAPIRateLimit(
                                max_requests_per_second=100,
                                min_interval_seconds=0,
                            ),
                            sleeper=lambda delay: None,
                        ),
                    ),
                    token_store=token_store,
                    platform_registry=registry,
                    sleeper=lambda delay: None,
                ),
                "dzen": BasePlatformAdapter(
                    platform="dzen",
                    publisher=DzenPostPublisher(
                        client=client,
                        api_base_url="https://dzen.example/api",
                    ),
                    token_store=token_store,
                    platform_registry=registry,
                    sleeper=lambda delay: None,
                ),
                "ok": BasePlatformAdapter(
                    platform="ok",
                    publisher=OKMediatopicPublisher(
                        client=client,
                        api_base_url="https://ok.example/fb.do",
                        application_secret_key=SecretStr("ok-stage5-secret"),
                    ),
                    token_store=token_store,
                    platform_registry=registry,
                    sleeper=lambda delay: None,
                ),
            },
        ).publish(
            PublicationBatchRequest(
                tenant_id=TENANT_ID,
                publication_id="pub-stage5",
                content="Готовый материал для этапа 5",
                correlation_id="corr-stage5-publication",
                metadata={
                    "dzen": {"title": "Дайджест интеграций"},
                    "vk": {"guid": "pub-stage5"},
                },
            )
        )

    assert [request.url.host for request in requests] == [
        "api.vk.com",
        "dzen.example",
        "ok.example",
    ]
    return result, Stage5PublicationState(
        proxy_provider=proxy_provider,
        fallback_registry=fallback_registry,
        telegram_primary=telegram_primary,
        telegram_fallback=telegram_fallback,
    )


async def _execute_rf_gateway_payout() -> tuple[
    PayoutPaymentStatusReceipt,
    Stage5PayoutState,
]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST" and request.url.path == "/api/payouts":
            payload = json.loads(request.content.decode("utf-8"))
            assert request.headers["Authorization"] == "Bearer rf-stage5-token"
            assert request.headers["Idempotency-Key"] == "execution-stage5"
            assert payload["merchant_id"] == "merchant-stage5"
            assert payload["payout_id"] == PAYOUT_ID
            assert payload["amount_minor"] == 250_000
            assert payload["currency"] == "RUB"
            assert payload["recipient_token"] == "recipient-token-stage5"
            assert "member_id" not in payload
            return httpx.Response(
                202,
                json={
                    "payment_id": "rfpay-stage5",
                    "status": "accepted",
                    "accepted_at": "2026-06-20T20:01:00Z",
                },
            )

        if request.method == "GET" and request.url.path == "/api/payouts/rfpay-stage5":
            return httpx.Response(
                200,
                json={
                    "payment_id": "rfpay-stage5",
                    "status": "succeeded",
                    "synced_at": "2026-06-20T20:05:00Z",
                },
            )

        return httpx.Response(404, json={"error_code": "unexpected_request"})

    bus = InMemoryEventBus()
    audit_sink = InMemoryAuditLogSink()
    audit_logger = AuditLogger(sink=audit_sink)
    blockchain_auditor = InMemoryBlockchainAuditConnector()
    now = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)
    queue_manager = await _confirmed_queue_manager(
        bus=bus,
        audit_logger=audit_logger,
        now=now,
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = RFPayoutGatewayConnector(
            config=RFPayoutGatewayConfig(
                base_url="https://rf-pay.example/api",
                merchant_id="merchant-stage5",
                api_key=SecretStr("rf-stage5-token"),
            ),
            client=client,
        )
        execution_manager = PayoutExecutionManager(
            repository=queue_manager.repository,
            publisher=bus,
            audit_logger=audit_logger,
            payment_connector=connector,
            blockchain_auditor=blockchain_auditor,
        )

        await execution_manager.execute_payout(
            tenant_id=TENANT_ID,
            payout_id=PAYOUT_ID,
            correlation_id="corr-stage5-payout",
            execution_id="execution-stage5",
            event_id="evt-stage5-payout-executed",
            notification_id="notification-stage5",
            now=now + timedelta(hours=8, minutes=1),
            metadata={
                "payment": {
                    "amount_minor": 250_000,
                    "currency": "RUB",
                    "recipient_token": "recipient-token-stage5",
                    "rails": "sbp",
                    "purpose": "stage 5 acceptance payout",
                },
                "stage": "5",
            },
        )
        status = await execution_manager.sync_payment_status(
            tenant_id=TENANT_ID,
            payout_id=PAYOUT_ID,
            correlation_id="corr-stage5-payout",
            event_id="evt-stage5-payment-status",
            now=now + timedelta(hours=8, minutes=5),
        )

    assert [request.url.path for request in requests] == [
        "/api/payouts",
        "/api/payouts/rfpay-stage5",
    ]
    return status, Stage5PayoutState(
        bus=bus,
        audit_sink=audit_sink,
        blockchain_auditor=blockchain_auditor,
        payout=queue_manager.get_payout(tenant_id=TENANT_ID, payout_id=PAYOUT_ID),
    )


async def _confirmed_queue_manager(
    *,
    bus: InMemoryEventBus,
    audit_logger: AuditLogger,
    now: datetime,
) -> PayoutQueueManager:
    queue_manager = PayoutQueueManager(
        publisher=bus,
        audit_logger=audit_logger,
    )
    await queue_manager.queue_payout(
        tenant_id=TENANT_ID,
        member_id="member-stage5",
        period="2026-06",
        payout_share=0.42,
        distribution_id="distribution-stage5",
        distribution_hash="a" * 64,
        created_by="council-1",
        correlation_id="corr-stage5-payout",
        payout_id=PAYOUT_ID,
        event_id="evt-stage5-payout-queued",
        now=now,
    )
    context = TenantContext(
        tenant_id=TENANT_ID,
        subject="council-2",
        roles=("council",),
        correlation_id="corr-stage5-payout",
    )
    confirmation_at = now + timedelta(minutes=1)
    two_factor = TOTPService(clock=lambda: confirmation_at.timestamp())
    confirmation = two_factor.confirm_sensitive_operation(
        context=context,
        secret=TOTP_SECRET,
        code=two_factor.generate_totp(TOTP_SECRET),
        operation=PAYOUT_CONFIRM_OPERATION,
        resource_id=PAYOUT_ID,
    )
    confirmation_manager = PayoutConfirmationManager(
        repository=queue_manager.repository,
        publisher=bus,
        audit_logger=audit_logger,
    )
    await confirmation_manager.confirm_payout(
        tenant_id=TENANT_ID,
        payout_id=PAYOUT_ID,
        context=context,
        two_factor_confirmation=confirmation,
        confirmation_id="confirmation-stage5",
        event_id="evt-stage5-payout-confirmed",
    )
    return queue_manager


def _platform_entry(
    platform: str,
    *,
    priority: int,
    target_id: str,
) -> PlatformRegistryEntry:
    return PlatformRegistryEntry(
        tenant_id=TENANT_ID,
        platform=platform,
        status=PlatformStatus.ACTIVE,
        priority=priority,
        limits=PlatformContentLimits(max_text_length=5000, max_media_items=10),
        parameters={"default_target_id": target_id},
    )


def _assert_private_blockchain_network_declared() -> None:
    compose = (ROOT / "infra/blockchain/docker-compose.yml").read_text(encoding="utf-8")
    network = (ROOT / "infra/blockchain/qbft-network.json").read_text(encoding="utf-8")
    entries = default_platform_registry_entries(tenant_id=TENANT_ID)

    assert len(entries) == DEFAULT_PLATFORM_CATALOG_SIZE == 102
    assert [entry.platform for entry in entries[:4]] == [
        "telegram",
        "vk",
        "dzen",
        "ok",
    ]
    for marker in (
        "besu-validator-1:",
        "besu-validator-4:",
        "besu-rpc",
        "besu-qbft-network:",
        'BESU_RPC_HTTP_ENABLED: "false"',
    ):
        assert marker in compose

    assert '"chainId": 20260679' in network
    assert '"qbft"' in network


def _assert_sensitive_values_absent(
    *,
    publication: PublicationBatchResult,
    payout_state: Stage5PayoutState,
) -> None:
    serialized = "\n".join(
        [
            publication.model_dump_json(),
            *[record.model_dump_json() for record in payout_state.audit_sink.records],
            *[message.envelope.to_json() for message in payout_state.bus.messages],
            *[
                record.model_dump_json()
                for record in payout_state.blockchain_auditor.records
            ],
        ]
    )
    for forbidden in (
        "telegram-stage5-token",
        "vk-stage5-token",
        "dzen-stage5-token",
        "ok-stage5-token",
        "rf-stage5-token",
        "recipient-token-stage5",
        "vault://tenant-a",
        "proxy-stage5.example",
        "amount_minor",
        "recipient_token",
    ):
        assert forbidden not in serialized


class ScriptedPublisher:
    def __init__(
        self,
        *,
        outcomes: list[PlatformPublishResult | PlatformPublicationError],
    ) -> None:
        self._outcomes = outcomes
        self.commands: list[PlatformPublishCommand] = []

    async def publish(
        self,
        command: PlatformPublishCommand,
    ) -> PlatformPublishResult:
        self.commands.append(command)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, PlatformPublicationError):
            raise outcome

        return outcome


class ScriptedFallbackPublisher(FallbackChannelPublisher):
    def __init__(self, *, failing_channels: set[str] | None = None) -> None:
        self._failing_channels = failing_channels or set()
        self.attempted_channel_ids: list[str] = []

    async def publish(
        self,
        command: PlatformPublishCommand,
        route: FallbackChannelRoute,
    ) -> PlatformPublishResult:
        self.attempted_channel_ids.append(route.channel_id)
        if route.channel_id in self._failing_channels:
            raise PlatformPublicationError(
                "fallback channel is unavailable",
                platform=command.platform,
                error_code="fallback_channel_unavailable",
                retryable=True,
            )

        return PlatformPublishResult(
            platform=command.platform,
            platform_ref=f"{route.channel_type.value}:{route.channel_id}:stage5",
            connector_name=f"{route.channel_type.value}_fallback",
            published_at=datetime(2026, 6, 20, 12, 0, tzinfo=UTC),
        )
