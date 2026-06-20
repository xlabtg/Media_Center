from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from messenger_adapter import (
    BasePlatformAdapter,
    InMemoryPlatformTokenStore,
    InMemoryTelegramMemberContextProvider,
    InMemoryTelegramTelethonSessionStore,
    PlatformTokenCipher,
    PublicationRequest,
    RetryPolicy,
    TelegramClientGateway,
    TelegramClientScenario,
    TelegramIdentityCipher,
    TelegramMemberSnapshot,
    TelegramTelethonClient,
    TelegramTelethonClientConfig,
    TelegramTelethonClientFactory,
    TelegramTelethonInboundBridge,
    TelegramTelethonPollRequest,
    TelegramTelethonPublisher,
    TelegramTelethonRateLimit,
    TelegramTelethonRateLimiter,
    TelegramTelethonSessionClientProvider,
    TelegramTelethonSessionNotFoundError,
)
from pydantic import SecretStr

from libs.shared import InMemoryEventBus, TenantIsolationError

TENANT_ID = "tenant-a"
MEMBER_ID = "member-1"
TELEGRAM_USER_ID = "10987654321"
SESSION_REF = "primary-session"
RAW_SESSION = "telethon-string-session-secret"
ROOT = Path(__file__).resolve().parents[1]


def _encryption_key() -> str:
    return base64.b64encode(b"5" * 32).decode("ascii")


def test_issue_75_telethon_sessions_are_encrypted_and_tenant_scoped() -> None:
    store = InMemoryTelegramTelethonSessionStore(PlatformTokenCipher(_encryption_key()))

    record = store.save_session(
        tenant_id=TENANT_ID,
        session_ref=SESSION_REF,
        session_string=RAW_SESSION,
        metadata={"purpose": "acceptance"},
        saved_at=datetime(2026, 6, 20, 12, 0, tzinfo=UTC),
    )

    assert record.session_encrypted.startswith("aes256gcm:")
    assert RAW_SESSION not in record.model_dump_json()
    assert (
        store.decrypt_session(record, tenant_id=TENANT_ID).get_secret_value()
        == RAW_SESSION
    )

    with pytest.raises(TenantIsolationError):
        store.decrypt_session(record, tenant_id="tenant-b")

    with pytest.raises(TelegramTelethonSessionNotFoundError):
        store.require_session(tenant_id="tenant-b", session_ref=SESSION_REF)


def test_issue_75_telethon_publisher_retries_flood_wait_and_rotates_session() -> None:
    asyncio.run(_run_issue_75_publish_scenario())


async def _run_issue_75_publish_scenario() -> None:
    session_store = InMemoryTelegramTelethonSessionStore(
        PlatformTokenCipher(_encryption_key())
    )
    session_store.save_session(
        tenant_id=TENANT_ID,
        session_ref=SESSION_REF,
        session_string=RAW_SESSION,
    )
    token_store = InMemoryPlatformTokenStore(PlatformTokenCipher(_encryption_key()))
    token_store.save_token(
        tenant_id=TENANT_ID,
        platform="telegram",
        token=SESSION_REF,
    )

    fake_client = FakeTelethonClient(
        send_outcomes=[
            FakeFloodWaitError(seconds=4),
            FakeTelethonMessage(
                id=42,
                chat_id=-100123,
                sender_id=None,
                raw_text="",
                date=datetime(2026, 6, 20, 12, 5, tzinfo=UTC),
            ),
        ],
        rotated_session="telethon-string-session-rotated",
    )
    fake_factory = FakeTelethonClientFactory(fake_client)
    provider = TelegramTelethonSessionClientProvider(
        session_store=session_store,
        api_id=12345,
        api_hash=SecretStr("api-hash-secret"),
        client_factory=fake_factory,
        session_extractor=lambda client: (
            client.rotated_session if isinstance(client, FakeTelethonClient) else None
        ),
    )
    bus = InMemoryEventBus()
    retry_delays: list[float] = []
    publisher = TelegramTelethonPublisher(
        client_provider=provider,
        rate_limiter=TelegramTelethonRateLimiter(
            limit=TelegramTelethonRateLimit(
                max_messages_per_minute=600,
                min_interval_seconds=0,
            ),
            sleeper=lambda delay: None,
        ),
    )
    adapter = BasePlatformAdapter(
        platform="telegram",
        publisher=publisher,
        token_store=token_store,
        retry_policy=RetryPolicy(max_attempts=2, initial_delay_seconds=1),
        event_publisher=bus,
        sleeper=retry_delays.append,
    )

    receipt = await adapter.publish(
        PublicationRequest(
            tenant_id=TENANT_ID,
            platform="telegram",
            publication_id="pub-issue-75",
            target_id="@nmc_channel",
            content="Готовый материал через Telethon",
            correlation_id="corr-issue-75",
            metadata={"telegram": {"session_ref": SESSION_REF, "silent": True}},
        )
    )

    assert receipt.attempt_count == 2
    assert receipt.platform_ref_hash.startswith("sha256:")
    assert retry_delays == [4.0]
    assert fake_client.connected_count == 2
    assert fake_client.disconnected_count == 2
    assert [message["entity"] for message in fake_client.sent_messages] == [
        "@nmc_channel",
        "@nmc_channel",
    ]
    assert fake_client.sent_messages[-1]["kwargs"] == {"silent": True}
    assert [config.session_string for config in fake_factory.configs] == [
        RAW_SESSION,
        RAW_SESSION,
    ]
    rotated = session_store.require_session(
        tenant_id=TENANT_ID,
        session_ref=SESSION_REF,
    )
    assert (
        session_store.decrypt_session(rotated, tenant_id=TENANT_ID).get_secret_value()
        == "telethon-string-session-rotated"
    )
    assert RAW_SESSION not in bus.messages[-1].envelope.to_json()


def test_issue_75_telethon_rate_limiter_paces_per_target() -> None:
    asyncio.run(_run_issue_75_rate_limit_scenario())


async def _run_issue_75_rate_limit_scenario() -> None:
    recorded_delays: list[float] = []
    limiter = TelegramTelethonRateLimiter(
        limit=TelegramTelethonRateLimit(
            max_messages_per_minute=60,
            min_interval_seconds=5,
        ),
        sleeper=recorded_delays.append,
    )
    first_at = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)

    first_delay = await limiter.acquire(
        tenant_id=TENANT_ID,
        session_ref=SESSION_REF,
        target_ref="@nmc_channel",
        action="publish",
        now=first_at,
    )
    second_delay = await limiter.acquire(
        tenant_id=TENANT_ID,
        session_ref=SESSION_REF,
        target_ref="@nmc_channel",
        action="publish",
        now=first_at + timedelta(seconds=1),
    )
    another_target_delay = await limiter.acquire(
        tenant_id=TENANT_ID,
        session_ref=SESSION_REF,
        target_ref="@another_channel",
        action="publish",
        now=first_at + timedelta(seconds=1),
    )

    assert first_delay == 0
    assert second_delay == 4
    assert another_target_delay == 0
    assert recorded_delays == [4]


def test_issue_75_telethon_reads_updates_and_replies_via_gateway() -> None:
    asyncio.run(_run_issue_75_inbound_scenario())


async def _run_issue_75_inbound_scenario() -> None:
    session_store = InMemoryTelegramTelethonSessionStore(
        PlatformTokenCipher(_encryption_key())
    )
    session_store.save_session(
        tenant_id=TENANT_ID,
        session_ref=SESSION_REF,
        session_string=RAW_SESSION,
    )
    fake_client = FakeTelethonClient(
        inbound_messages=[
            FakeTelethonMessage(
                id=10,
                chat_id=-100123,
                sender_id=int(TELEGRAM_USER_ID),
                raw_text="/balance",
                date=datetime(2026, 6, 20, 12, 10, tzinfo=UTC),
            )
        ]
    )
    provider = TelegramTelethonSessionClientProvider(
        session_store=session_store,
        api_id=12345,
        api_hash=SecretStr("api-hash-secret"),
        client_factory=FakeTelethonClientFactory(fake_client),
        session_extractor=lambda client: None,
    )
    gateway = _build_gateway()
    await gateway.link_account(
        tenant_id=TENANT_ID,
        member_id=MEMBER_ID,
        telegram_user_id=TELEGRAM_USER_ID,
        correlation_id="corr-link-issue-75",
    )
    bridge = TelegramTelethonInboundBridge(
        client_provider=provider,
        gateway=gateway,
        rate_limiter=TelegramTelethonRateLimiter(
            limit=TelegramTelethonRateLimit(
                max_messages_per_minute=600,
                min_interval_seconds=0,
            ),
            sleeper=lambda delay: None,
        ),
    )

    result = await bridge.poll_once(
        TelegramTelethonPollRequest(
            tenant_id=TENANT_ID,
            session_ref=SESSION_REF,
            source="@nmc_updates",
            correlation_id="corr-poll-issue-75",
            limit=10,
        )
    )

    assert result.failed == ()
    assert len(result.handled) == 1
    assert result.handled[0].message_id == 10
    assert result.handled[0].scenario is TelegramClientScenario.BALANCE
    assert result.next_offset_id == 10
    assert fake_client.iter_requests == [
        {"entity": "@nmc_updates", "kwargs": {"limit": 10}}
    ]
    assert fake_client.sent_messages[-1]["entity"] == "-100123"
    assert fake_client.sent_messages[-1]["kwargs"] == {"reply_to": 10}
    assert "Баллы: 4242" in str(fake_client.sent_messages[-1]["message"])

    result_json = result.model_dump_json()
    assert "/balance" not in result_json
    assert TELEGRAM_USER_ID not in result_json
    assert RAW_SESSION not in result_json


def test_issue_75_telethon_docs_are_marked_implemented() -> None:
    spec = (ROOT / "docs/modules/messenger-adapter.md").read_text(encoding="utf-8")
    readme = (ROOT / "services/messenger-adapter/README.md").read_text(encoding="utf-8")
    security = (ROOT / "docs/SECURITY.md").read_text(encoding="utf-8")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    for marker in (
        "#75",
        "TelegramTelethonPublisher",
        "TelegramTelethonInboundBridge",
        "TelegramTelethonSessionClientProvider",
        "telegram_telethon_session",
        "Telethon 1.44.0",
    ):
        assert marker in spec

    for marker in (
        "Telegram через Telethon",
        "TelegramTelethonPublisher",
        "InMemoryTelegramTelethonSessionStore",
        "session_ref",
    ):
        assert marker in readme

    assert "Telethon-сессии Telegram" in security
    assert "telegram_telethon_session" in security
    assert "Telethon==1.44.0" in pyproject


def _build_gateway() -> TelegramClientGateway:
    members = InMemoryTelegramMemberContextProvider()
    members.save(
        TelegramMemberSnapshot(
            tenant_id=TENANT_ID,
            member_id=MEMBER_ID,
            status_label="Действительный участник",
            contribution_weight=1.75,
            points_balance=4242,
            open_task_titles=("Проверить материал",),
        )
    )
    return TelegramClientGateway(
        identity_cipher=TelegramIdentityCipher(_encryption_key()),
        member_provider=members,
        event_publisher=InMemoryEventBus(),
    )


class FakeFloodWaitError(Exception):
    def __init__(self, *, seconds: int) -> None:
        super().__init__("Flood wait")
        self.seconds = seconds


@dataclass(slots=True)
class FakeTelethonMessage:
    id: int
    chat_id: int
    sender_id: int | None
    raw_text: str
    date: datetime


@dataclass(slots=True)
class FakeTelethonClient:
    send_outcomes: list[object | Exception] = field(default_factory=list)
    inbound_messages: list[FakeTelethonMessage] = field(default_factory=list)
    authorized: bool = True
    rotated_session: str | None = None
    sent_messages: list[dict[str, object]] = field(default_factory=list, init=False)
    iter_requests: list[dict[str, object]] = field(default_factory=list, init=False)
    connected_count: int = 0
    disconnected_count: int = 0

    async def connect(self) -> None:
        self.connected_count += 1

    async def disconnect(self) -> None:
        self.disconnected_count += 1

    async def is_user_authorized(self) -> bool:
        return self.authorized

    async def send_message(
        self,
        entity: object,
        message: str,
        **kwargs: object,
    ) -> object:
        self.sent_messages.append(
            {
                "entity": entity,
                "message": message,
                "kwargs": dict(kwargs),
            }
        )
        if self.send_outcomes:
            outcome = self.send_outcomes.pop(0)
        else:
            outcome = FakeTelethonMessage(
                id=1000 + len(self.sent_messages),
                chat_id=_fake_chat_id(entity),
                sender_id=None,
                raw_text="",
                date=datetime(2026, 6, 20, 12, 0, tzinfo=UTC),
            )
        if isinstance(outcome, Exception):
            raise outcome

        return outcome

    async def iter_messages(
        self,
        entity: object,
        **kwargs: object,
    ) -> AsyncIterator[object]:
        self.iter_requests.append({"entity": entity, "kwargs": dict(kwargs)})
        for message in self.inbound_messages:
            yield message


@dataclass(slots=True)
class FakeTelethonClientFactory(TelegramTelethonClientFactory):
    client: FakeTelethonClient
    configs: list[TelegramTelethonClientConfig] = field(default_factory=list)

    def create_client(
        self,
        config: TelegramTelethonClientConfig,
    ) -> TelegramTelethonClient:
        self.configs.append(config)
        return self.client


def _fake_chat_id(entity: object) -> int:
    value = str(entity)
    if value.lstrip("-").isdigit():
        return int(value)

    return -100000
