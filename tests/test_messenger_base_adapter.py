from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from messenger_adapter import (
    BasePlatformAdapter,
    InMemoryPlatformTokenStore,
    PlatformPublicationError,
    PlatformPublishCommand,
    PlatformPublishResult,
    PlatformTokenCipher,
    PlatformTokenNotFoundError,
    PublicationRequest,
    RetryPolicy,
)

from libs.shared import (
    AuditLogger,
    InMemoryAuditLogSink,
    InMemoryEventBus,
    TenantIsolationError,
)


def _encryption_key() -> str:
    return base64.b64encode(b"0" * 32).decode("ascii")


def test_platform_token_store_encrypts_tokens_and_isolates_tenants() -> None:
    token_store = InMemoryPlatformTokenStore(PlatformTokenCipher(_encryption_key()))

    record = token_store.save_token(
        tenant_id="tenant-a",
        platform="telegram",
        token="tg-secret-token",
        token_id="primary",
    )

    assert record.token_encrypted.startswith("aes256gcm:")
    assert "tg-secret-token" not in record.token_encrypted
    decrypted_token = token_store.decrypt_token(record, tenant_id="tenant-a")
    assert decrypted_token.get_secret_value() == "tg-secret-token"

    with pytest.raises(TenantIsolationError):
        token_store.decrypt_token(record, tenant_id="tenant-b")

    with pytest.raises(PlatformTokenNotFoundError):
        token_store.require_token(tenant_id="tenant-b", platform="telegram")


def test_base_adapter_retries_publication_and_never_emits_raw_token() -> None:
    asyncio.run(_run_retry_scenario())


async def _run_retry_scenario() -> None:
    publisher = ScriptedPublisher(
        outcomes=[
            PlatformPublicationError(
                "rate limit",
                platform="telegram",
                error_code="rate_limited",
                retryable=True,
            ),
            PlatformPublicationError(
                "temporary outage",
                platform="telegram",
                error_code="platform_unavailable",
                retryable=True,
            ),
            PlatformPublishResult(
                platform="telegram",
                platform_ref="telegram-message-42",
                connector_name="telegram_mock",
                published_at=datetime(2026, 6, 18, 12, 0, tzinfo=UTC),
            ),
        ],
    )
    token_store = InMemoryPlatformTokenStore(PlatformTokenCipher(_encryption_key()))
    token_store.save_token(
        tenant_id="tenant-a",
        platform="telegram",
        token="tg-secret-token",
    )
    bus = InMemoryEventBus()
    audit_sink = InMemoryAuditLogSink()
    recorded_delays: list[float] = []
    adapter = BasePlatformAdapter(
        platform="telegram",
        publisher=publisher,
        token_store=token_store,
        retry_policy=RetryPolicy(
            max_attempts=3,
            initial_delay_seconds=0.5,
            multiplier=2,
            max_delay_seconds=2,
        ),
        event_publisher=bus,
        audit_logger=AuditLogger(sink=audit_sink),
        sleeper=recorded_delays.append,
    )

    receipt = await adapter.publish(
        PublicationRequest(
            tenant_id="tenant-a",
            platform="telegram",
            publication_id="pub-1",
            target_id="channel-1",
            content="Готовый материал для публикации",
            correlation_id="corr-1",
            metadata={"content_id": "content-1"},
        ),
        event_id="evt-publication-succeeded-1",
    )

    assert receipt.attempt_count == 3
    assert receipt.platform_ref_hash.startswith("sha256:")
    assert recorded_delays == [0.5, 1.0]
    assert [command.attempt for command in publisher.commands] == [1, 2, 3]
    assert {
        command.access_token.get_secret_value() for command in publisher.commands
    } == {"tg-secret-token"}
    assert bus.messages[-1].routing_key == "tenant.tenant-a.publication.succeeded"
    assert bus.messages[-1].envelope.payload == {
        "publication_id": "pub-1",
        "platform": "telegram",
        "target_id": "channel-1",
        "platform_ref_hash": receipt.platform_ref_hash,
        "attempt_count": 3,
        "audit_hash": receipt.audit_hash,
    }
    assert "tg-secret-token" not in bus.messages[-1].envelope.to_json()
    assert "tg-secret-token" not in str(audit_sink.records[-1].metadata)


def test_base_adapter_stops_after_retry_policy_is_exhausted() -> None:
    asyncio.run(_run_retry_exhausted_scenario())


async def _run_retry_exhausted_scenario() -> None:
    publisher = ScriptedPublisher(
        outcomes=[
            PlatformPublicationError(
                "platform is unavailable",
                platform="vk",
                error_code="platform_unavailable",
                retryable=True,
            ),
            PlatformPublicationError(
                "platform is unavailable",
                platform="vk",
                error_code="platform_unavailable",
                retryable=True,
            ),
        ],
    )
    token_store = InMemoryPlatformTokenStore(PlatformTokenCipher(_encryption_key()))
    token_store.save_token(tenant_id="tenant-a", platform="vk", token="vk-secret-token")
    bus = InMemoryEventBus()
    recorded_delays: list[float] = []
    adapter = BasePlatformAdapter(
        platform="vk",
        publisher=publisher,
        token_store=token_store,
        retry_policy=RetryPolicy(max_attempts=2, initial_delay_seconds=1),
        event_publisher=bus,
        sleeper=recorded_delays.append,
    )

    with pytest.raises(PlatformPublicationError) as exc_info:
        await adapter.publish(
            PublicationRequest(
                tenant_id="tenant-a",
                platform="vk",
                publication_id="pub-2",
                target_id="group-1",
                content="Материал для VK",
                correlation_id="corr-2",
            ),
            failure_event_id="evt-publication-failed-1",
        )

    assert exc_info.value.error_code == "platform_unavailable"
    assert exc_info.value.attempt_count == 2
    assert len(publisher.commands) == 2
    assert recorded_delays == [1.0]
    assert bus.messages[-1].routing_key == "tenant.tenant-a.publication.failed"
    assert bus.messages[-1].envelope.payload == {
        "publication_id": "pub-2",
        "platform": "vk",
        "target_id": "group-1",
        "error_code": "platform_unavailable",
        "retryable": True,
        "attempt_count": 2,
        "audit_hash": exc_info.value.audit_hash,
    }
    assert "vk-secret-token" not in bus.messages[-1].envelope.to_json()


@dataclass(slots=True)
class ScriptedPublisher:
    outcomes: list[PlatformPublishResult | PlatformPublicationError]
    commands: list[PlatformPublishCommand] = field(default_factory=list)

    async def publish(
        self,
        command: PlatformPublishCommand,
    ) -> PlatformPublishResult:
        self.commands.append(command)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, PlatformPublicationError):
            raise outcome

        return outcome
