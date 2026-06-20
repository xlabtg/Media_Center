from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime

from messenger_adapter import (
    InMemoryTelegramMemberContextProvider,
    InMemoryTelegramProxyDirectory,
    TelegramClientGateway,
    TelegramIdentityCipher,
    TelegramInboundMessage,
    TelegramMemberSnapshot,
    TelegramProxyEndpoint,
    TelegramProxyProtocol,
    TelegramProxyRotator,
)

from libs.shared import InMemoryEventBus


def _key() -> str:
    return base64.b64encode(b"1" * 32).decode("ascii")


async def main() -> None:
    bus = InMemoryEventBus()
    members = InMemoryTelegramMemberContextProvider()
    members.save(
        TelegramMemberSnapshot(
            tenant_id="tenant-a",
            member_id="member-1",
            status_label="Действительный участник",
            contribution_weight=1.75,
            points_balance=4200,
            open_task_titles=("Проверить материал", "Согласовать тему"),
        )
    )
    proxies = InMemoryTelegramProxyDirectory()
    proxies.register(
        TelegramProxyRotator(
            tenant_id="tenant-a",
            pool_id="pool-a",
            endpoints=[
                TelegramProxyEndpoint(
                    proxy_id="proxy-http",
                    protocol=TelegramProxyProtocol.HTTP,
                    url="https://proxy-a.example:8443",
                    secret_ref="vault:tenant-a/proxy-a",
                    priority=10,
                ),
                TelegramProxyEndpoint(
                    proxy_id="proxy-socks",
                    protocol=TelegramProxyProtocol.SOCKS5,
                    url="socks5://proxy-b.example:1080",
                    priority=20,
                ),
            ],
        )
    )
    gateway = TelegramClientGateway(
        identity_cipher=TelegramIdentityCipher(_key()),
        member_provider=members,
        proxy_directory=proxies,
        event_publisher=bus,
    )

    await gateway.link_account(
        tenant_id="tenant-a",
        member_id="member-1",
        telegram_user_id="998877",
        correlation_id="corr-link",
        linked_at=datetime(2026, 6, 20, 9, 0, tzinfo=UTC),
    )

    for text in ("/start", "/help", "/status", "/balance", "/tasks", "/whoami"):
        exchange = await gateway.handle_update(
            TelegramInboundMessage(
                tenant_id="tenant-a",
                telegram_user_id="998877",
                text=text,
                correlation_id="corr-cmd",
            ),
            now=datetime(2026, 6, 20, 9, 5, tzinfo=UTC),
        )
        proxy = exchange.proxy_lease
        proxy_id = proxy.proxy_id if proxy else None
        redacted = proxy.redacted_url if proxy else None
        print(
            f"{text:10} -> {exchange.scenario.value:8} "
            f"proxy={proxy_id} redacted={redacted}"
        )
        print(f"    reply: {exchange.reply.text.splitlines()[0]}")

    last_json = bus.messages[-1].envelope.to_json()
    assert "998877" not in last_json, "RAW TELEGRAM ID LEAKED"
    assert "vault:tenant-a/proxy-a" not in last_json, "SECRET_REF LEAKED"
    print("\nEvents published:", len(bus.messages))
    print("No raw id / secret in last event:", "998877" not in last_json)
    print("Last event type:", bus.messages[-1].envelope.type)


asyncio.run(main())
