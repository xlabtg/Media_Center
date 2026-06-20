"""Детерминированное воспроизведение флэйки-ассерта из issue #71.

Приёмочный тест проверял ``assert "4242" not in events.to_json()``. Баланс
``4242`` — четыре десятичные цифры, то есть валидные hex-символы. Они могут
случайно встретиться как подстрока в SHA-256 хэшах или сгенерированных UUID
(``event_id``/``link_id``/``lease_id``), которые попадают в сериализованное
событие. Локально совпадения не было, а в CI один из UUID содержал ``4242`` —
и тест падал.

Здесь мы детерминированно подменяем генератор UUID так, чтобы он выдавал
значение с подстрокой ``4242``, и показываем:

* старый скан полного ``to_json()`` ловит ложное срабатывание (падал бы);
* новый скан payload с вырезанными криптографическими токенами — чист.

Запуск: ``python experiments/telegram_leak_scan_flaky_repro.py``.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import uuid
from datetime import UTC, datetime

import messenger_adapter.telegram_client as tc
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

BALANCE = 4242
TELEGRAM_ID = "10987654321"
# UUID, в котором гарантированно присутствует подстрока баланса "4242".
POISON_UUID = uuid.UUID("42421111-2222-4333-8444-555566667777")

_OPAQUE_TOKEN = re.compile(
    r"sha256:[0-9a-f]+"
    r"|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)


def _key() -> str:
    return base64.b64encode(b"1" * 32).decode("ascii")


def _build_gateway(bus: InMemoryEventBus) -> TelegramClientGateway:
    members = InMemoryTelegramMemberContextProvider()
    members.save(
        TelegramMemberSnapshot(
            tenant_id="tenant-a",
            member_id="member-1",
            status_label="Действительный участник",
            contribution_weight=1.75,
            points_balance=BALANCE,
            open_task_titles=("Проверить материал",),
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
                ),
            ],
        )
    )
    return TelegramClientGateway(
        identity_cipher=TelegramIdentityCipher(_key()),
        member_provider=members,
        proxy_directory=proxies,
        event_publisher=bus,
    )


def _payload_leak_surface(bus: InMemoryEventBus) -> str:
    fragments: list[str] = []
    for message in bus.messages:
        payload_json = json.dumps(message.envelope.payload, ensure_ascii=False)
        fragments.append(_OPAQUE_TOKEN.sub("<opaque>", payload_json))
    return "\n".join(fragments)


async def main() -> None:
    bus = InMemoryEventBus()
    gateway = _build_gateway(bus)

    # Подменяем uuid4 на детерминированный UUID с подстрокой "4242".
    original_uuid4 = uuid.uuid4
    tc.uuid4 = lambda: POISON_UUID  # type: ignore[assignment]
    try:
        await gateway.link_account(
            tenant_id="tenant-a",
            member_id="member-1",
            telegram_user_id=TELEGRAM_ID,
            correlation_id="corr-link",
            linked_at=datetime(2026, 6, 20, 9, 0, tzinfo=UTC),
        )
        await gateway.handle_update(
            TelegramInboundMessage(
                tenant_id="tenant-a",
                telegram_user_id=TELEGRAM_ID,
                text="/balance",
                correlation_id="corr-cmd",
            ),
            now=datetime(2026, 6, 20, 9, 5, tzinfo=UTC),
        )
    finally:
        tc.uuid4 = original_uuid4  # type: ignore[assignment]

    full_json = "\n".join(m.envelope.to_json() for m in bus.messages)
    payload_text = _payload_leak_surface(bus)

    old_hit = "4242" in full_json
    new_hit = "4242" in payload_text

    print("Старый скан full to_json() содержит '4242':", old_hit, "(ложное срабатывание)")
    print("Новый скан payload (без токенов) содержит '4242':", new_hit)
    print()
    assert old_hit, "Не удалось воспроизвести ложное срабатывание"
    assert not new_hit, "Новый скан не должен давать ложных срабатываний"
    print("Воспроизведено: старый ассерт флэйкует, новый — устойчив ✅")


if __name__ == "__main__":
    asyncio.run(main())
