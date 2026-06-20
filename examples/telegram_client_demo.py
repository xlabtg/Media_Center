"""Демонстрация Telegram-клиента участника НМЦ (issue #71).

Пример показывает три приёмочных критерия задачи на одном сквозном сценарии:

1. **Базовые сценарии через Telegram** — связывание аккаунта и обработка
   команд ``/start``, ``/help``, ``/status``, ``/balance``, ``/tasks``.
2. **Защищённая передача данных** — Telegram-идентичность шифруется
   AES-256-GCM, а в события/аудит попадают только хэши и шифртекст.
3. **Работа через прокси** — tenant-scoped пул ``http``/``socks5``/``mtproto``
   с round-robin и health-failover; наружу отдаются только ``redacted_url`` и
   SHA-256 хэши, учётные данные хранятся как ``secret_ref``.

Запуск: ``python examples/telegram_client_demo.py`` (PYTHONPATH должен включать
``services/messenger-adapter``, как в ``pyproject`` pytest ``pythonpath``).
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
from datetime import UTC, datetime

from messenger_adapter import (
    InMemoryTelegramMemberContextProvider,
    InMemoryTelegramProxyDirectory,
    TelegramClientExchange,
    TelegramClientGateway,
    TelegramIdentityCipher,
    TelegramInboundMessage,
    TelegramMemberSnapshot,
    TelegramProxyEndpoint,
    TelegramProxyProtocol,
    TelegramProxyRotator,
)

from libs.shared import InMemoryEventBus

TENANT_ID = "tenant-a"
MEMBER_ID = "member-1"
TELEGRAM_USER_ID = "10987654321"
CONTRIBUTION_WEIGHT = 1.75
POINTS_BALANCE = 4200
LINKED_AT = datetime(2026, 6, 20, 9, 0, tzinfo=UTC)
RECEIVED_AT = datetime(2026, 6, 20, 9, 5, tzinfo=UTC)
DEMO_COMMANDS = ("/start", "/help", "/status", "/balance", "/tasks", "/whoami")


def _demo_encryption_key() -> str:
    """Возвращает демонстрационный 32-байтовый ключ AES-256 в base64.

    В реальной среде ключ берётся из секрет-менеджера, а не из кода.
    """

    return base64.b64encode(b"1" * 32).decode("ascii")


def _build_member_provider() -> InMemoryTelegramMemberContextProvider:
    provider = InMemoryTelegramMemberContextProvider()
    provider.save(
        TelegramMemberSnapshot(
            tenant_id=TENANT_ID,
            member_id=MEMBER_ID,
            status_label="Действительный участник",
            contribution_weight=CONTRIBUTION_WEIGHT,
            points_balance=POINTS_BALANCE,
            open_task_titles=("Проверить материал", "Согласовать тему"),
        )
    )
    return provider


def _build_proxy_directory() -> InMemoryTelegramProxyDirectory:
    directory = InMemoryTelegramProxyDirectory()
    directory.register(
        TelegramProxyRotator(
            tenant_id=TENANT_ID,
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
                    secret_ref="vault:tenant-a/proxy-b",
                    priority=20,
                ),
                TelegramProxyEndpoint(
                    proxy_id="proxy-mtproto",
                    protocol=TelegramProxyProtocol.MTPROTO,
                    url="mtproto://proxy-c.example:443",
                    priority=30,
                ),
            ],
        )
    )
    return directory


def build_gateway(*, event_publisher: InMemoryEventBus) -> TelegramClientGateway:
    """Собирает шлюз Telegram-клиента с in-memory зависимостями для демо."""

    return TelegramClientGateway(
        identity_cipher=TelegramIdentityCipher(_demo_encryption_key()),
        member_provider=_build_member_provider(),
        proxy_directory=_build_proxy_directory(),
        event_publisher=event_publisher,
    )


def _format_exchange(command: str, exchange: TelegramClientExchange) -> str:
    proxy = exchange.proxy_lease
    proxy_id = proxy.proxy_id if proxy is not None else "—"
    redacted = proxy.redacted_url if proxy is not None else "—"
    reply_line = exchange.reply.text.splitlines()[0]
    return (
        f"{command:<8} → сценарий={exchange.scenario.value:<7} "
        f"proxy={proxy_id:<13} redacted={redacted}\n"
        f"           ответ: {reply_line}"
    )


async def run_demo() -> InMemoryEventBus:
    """Выполняет сквозной сценарий и возвращает шину с опубликованными событиями."""

    bus = InMemoryEventBus()
    gateway = build_gateway(event_publisher=bus)

    # 1. Связываем Telegram-аккаунт участника: сырой ID шифруется сразу.
    link = await gateway.link_account(
        tenant_id=TENANT_ID,
        member_id=MEMBER_ID,
        telegram_user_id=TELEGRAM_USER_ID,
        correlation_id="corr-link-demo",
        linked_at=LINKED_AT,
    )
    print("Аккаунт связан:")
    print(f"  telegram_user_ref_hash: {link.telegram_user_ref_hash}")
    print(f"  identity_encrypted:     {link.identity_encrypted[:32]}…")
    print(f"  сырой ID в шифртексте?   {TELEGRAM_USER_ID in link.identity_encrypted}")
    print()

    # 2. Обрабатываем базовые сценарии через прокси-ротацию (round-robin).
    print("Обработка команд (round-robin по живым proxy):")
    for command in DEMO_COMMANDS:
        exchange = await gateway.handle_update(
            TelegramInboundMessage(
                tenant_id=TENANT_ID,
                telegram_user_id=TELEGRAM_USER_ID,
                text=command,
                correlation_id="corr-cmd-demo",
            ),
            now=RECEIVED_AT,
        )
        print(_format_exchange(command, exchange))
    print()

    # 3. Health-failover: помечаем основной proxy нездоровым и проверяем,
    #    что выдача переключается на оставшиеся живые endpoint'ы.
    rotator = _build_proxy_directory().get(tenant_id=TENANT_ID)
    assert rotator is not None
    rotator.mark_unhealthy("proxy-http")
    leases = [rotator.lease().proxy_id for _ in range(3)]
    print("Health-failover после mark_unhealthy('proxy-http'):")
    print(f"  здоровых endpoint'ов: {rotator.healthy_count} из {rotator.total_count}")
    print(f"  выдача: {leases}")
    print()

    return bus


# Дайджесты (``sha256:``) и сгенерированные UUID состоят из hex-символов и
# могут случайно содержать числовые подстроки (баланс/ID). Перед сканом утечек
# такие непрозрачные токены вырезаются — иначе проверка флэйкует (issue #71).
_OPAQUE_TOKEN = re.compile(
    r"sha256:[0-9a-f]+|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)


def assert_no_sensitive_leak(bus: InMemoryEventBus) -> None:
    """Проверяет, что в payload событий не утекли сырой ID, секрет и баланс."""

    fragments: list[str] = []
    for message in bus.messages:
        payload_json = json.dumps(message.envelope.payload, ensure_ascii=False)
        fragments.append(_OPAQUE_TOKEN.sub("<opaque>", payload_json))
    payload_text = "\n".join(fragments)

    assert TELEGRAM_USER_ID not in payload_text, "Сырой Telegram ID утёк в событие"
    assert "vault:tenant-a/proxy-a" not in payload_text, "secret_ref утёк в событие"
    assert str(POINTS_BALANCE) not in payload_text, "Баланс участника утёк в событие"


async def main() -> None:
    bus = await run_demo()
    assert_no_sensitive_leak(bus)
    print("Опубликовано событий:", len(bus.messages))
    print("Типы событий:")
    for message in bus.messages:
        print(f"  - {message.envelope.type}")
    print("Утечки сырого ID / секрета / баланса: нет ✅")


if __name__ == "__main__":
    asyncio.run(main())
