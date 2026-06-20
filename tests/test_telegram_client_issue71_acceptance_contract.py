from __future__ import annotations

import asyncio
import base64
import json
import re
from pathlib import Path

import pytest
from messenger_adapter import (
    InMemoryTelegramMemberContextProvider,
    InMemoryTelegramProxyDirectory,
    PlatformTokenCipher,
    PlatformTokenCryptoError,
    TelegramAccountNotLinkedError,
    TelegramClientGateway,
    TelegramClientScenario,
    TelegramIdentityCipher,
    TelegramInboundMessage,
    TelegramMemberSnapshot,
    TelegramProxyConfigurationError,
    TelegramProxyEndpoint,
    TelegramProxyHealth,
    TelegramProxyProtocol,
    TelegramProxyRotator,
    TelegramProxyUnavailableError,
    parse_telegram_command,
    telegram_user_ref_hash,
)
from pydantic import ValidationError

from libs.shared import InMemoryEventBus

ROOT = Path(__file__).resolve().parents[1]
TENANT_A_TELEGRAM_ID = "10987654321"
TENANT_B_TELEGRAM_ID = "20987654322"


def _encryption_key() -> str:
    return base64.b64encode(b"1" * 32).decode("ascii")


# Криптографические дайджесты (``sha256:``) и сгенерированные UUID
# (``event_id``/``link_id``/``lease_id``) состоят из hex-символов и могут
# случайно содержать короткие числовые подстроки вроде баланса участника.
# Перед сканом утечек такие непрозрачные токены вырезаются, иначе проверка
# флэйкует на случайных UUID/хэшах (см. issue #71).
_OPAQUE_TOKEN = re.compile(
    r"sha256:[0-9a-f]+|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)


def _payload_leak_surface(bus: InMemoryEventBus) -> str:
    """Возвращает payload всех событий без криптографических токенов.

    Сканируется именно полезная нагрузка (``payload``), а не весь конверт:
    ``event_id`` и ``occurred_at`` — это метаданные конверта, формируемые
    шлюзом, и не несут данных участника. Дайджесты и UUID удаляются, чтобы
    остаток содержал только осмысленный plaintext полезной нагрузки.
    """

    fragments: list[str] = []
    for message in bus.messages:
        payload_json = json.dumps(message.envelope.payload, ensure_ascii=False)
        fragments.append(_OPAQUE_TOKEN.sub("<opaque>", payload_json))
    return "\n".join(fragments)


def test_issue_71_telegram_client_basic_scenarios_are_available() -> None:
    asyncio.run(_run_issue_71_basic_scenarios_scenario())


async def _run_issue_71_basic_scenarios_scenario() -> None:
    bus = InMemoryEventBus()
    gateway = _build_gateway(event_publisher=bus)

    link = await gateway.link_account(
        tenant_id="tenant-a",
        member_id="member-1",
        telegram_user_id=TENANT_A_TELEGRAM_ID,
        correlation_id="corr-link-a",
    )

    # Критерий «защищённая передача»: идентичность хранится только в виде
    # AES-256-GCM шифртекста и детерминированного tenant-scoped хэша.
    assert link.identity_encrypted.startswith("aes256gcm:")
    assert TENANT_A_TELEGRAM_ID not in link.identity_encrypted
    assert link.telegram_user_ref_hash == telegram_user_ref_hash(
        tenant_id="tenant-a",
        telegram_user_id=TENANT_A_TELEGRAM_ID,
    )

    scenarios = {}
    for text in ("/start", "/help", "/status", "/balance", "/tasks", "/whoami"):
        exchange = await gateway.handle_update(
            TelegramInboundMessage(
                tenant_id="tenant-a",
                telegram_user_id=TENANT_A_TELEGRAM_ID,
                text=text,
                correlation_id="corr-cmd-a",
            )
        )
        scenarios[text] = exchange

    # Критерий «базовые сценарии через Telegram».
    assert scenarios["/start"].scenario is TelegramClientScenario.START
    assert scenarios["/help"].scenario is TelegramClientScenario.HELP
    assert scenarios["/status"].scenario is TelegramClientScenario.STATUS
    assert scenarios["/balance"].scenario is TelegramClientScenario.BALANCE
    assert scenarios["/tasks"].scenario is TelegramClientScenario.TASKS
    assert scenarios["/whoami"].scenario is TelegramClientScenario.UNKNOWN

    assert "/help" in scenarios["/start"].reply.text
    assert "Статус участия: Действительный участник" in scenarios["/status"].reply.text
    assert "4242" in scenarios["/balance"].reply.text
    assert "Кв" in scenarios["/balance"].reply.text
    assert "Проверить материал" in scenarios["/tasks"].reply.text
    assert scenarios["/balance"].reply.contains_member_data is True
    assert scenarios["/help"].reply.contains_member_data is False

    # Каждое взаимодействие фиксируется аудит-хэшем и доменным событием.
    assert scenarios["/balance"].audit_hash
    assert len(bus.messages) == 7
    assert bus.messages[0].envelope.type == "messenger.telegram_client.account_linked"
    assert bus.messages[-1].envelope.type == "messenger.telegram_client.command_handled"

    # Чувствительные данные не утекают в полезную нагрузку событий: ни сырой
    # Telegram ID, ни баланс, ни статус участника. Криптографические токены
    # (хэши и UUID) исключаются из скана — иначе их hex-символы дают ложные
    # срабатывания на числовых подстроках вроде баланса (issue #71).
    payload_text = _payload_leak_surface(bus)
    assert TENANT_A_TELEGRAM_ID not in payload_text
    assert "4242" not in payload_text
    assert "Действительный участник" not in payload_text


def test_issue_71_telegram_client_encrypts_identity_per_tenant() -> None:
    asyncio.run(_run_issue_71_encryption_isolation_scenario())


async def _run_issue_71_encryption_isolation_scenario() -> None:
    gateway = _build_gateway()

    link_a = await gateway.link_account(
        tenant_id="tenant-a",
        member_id="member-1",
        telegram_user_id=TENANT_A_TELEGRAM_ID,
        correlation_id="corr-link-a",
    )
    link_b = await gateway.link_account(
        tenant_id="tenant-b",
        member_id="member-1",
        telegram_user_id=TENANT_A_TELEGRAM_ID,
        correlation_id="corr-link-b",
    )

    # Один и тот же Telegram ID в разных tenant даёт разные хэши и шифртексты.
    assert link_a.telegram_user_ref_hash != link_b.telegram_user_ref_hash
    assert link_a.identity_encrypted != link_b.identity_encrypted

    # AAD привязан к tenant: расшифровка чужим tenant невозможна.
    cipher = TelegramIdentityCipher(_encryption_key())
    assert (
        cipher.decrypt(
            tenant_id="tenant-a",
            identity_encrypted=link_a.identity_encrypted,
        )
        == TENANT_A_TELEGRAM_ID
    )
    with pytest.raises(PlatformTokenCryptoError):
        cipher.decrypt(
            tenant_id="tenant-b",
            identity_encrypted=link_a.identity_encrypted,
        )

    # Сообщение из другого tenant не находит привязку (изоляция хранилища).
    with pytest.raises(TelegramAccountNotLinkedError):
        await gateway.handle_update(
            TelegramInboundMessage(
                tenant_id="tenant-c",
                telegram_user_id=TENANT_A_TELEGRAM_ID,
                text="/status",
                correlation_id="corr-cmd-c",
            )
        )


def test_issue_71_telegram_client_rotates_proxy_and_handles_health() -> None:
    rotator = TelegramProxyRotator(
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
            TelegramProxyEndpoint(
                proxy_id="proxy-mtproto",
                protocol=TelegramProxyProtocol.MTPROTO,
                url="mtproto://proxy-c.example:443",
                priority=30,
            ),
        ],
    )

    assert rotator.protocols == ("http", "mtproto", "socks5")
    assert rotator.healthy_count == 3

    # Критерий «работа через прокси»: round-robin по живым endpoint.
    first = rotator.lease()
    second = rotator.lease()
    third = rotator.lease()
    fourth = rotator.lease()
    assert [lease.proxy_id for lease in (first, second, third, fourth)] == [
        "proxy-http",
        "proxy-socks",
        "proxy-mtproto",
        "proxy-http",
    ]

    # Утечки секретов нет: только redacted URL и SHA-256 хэши.
    assert first.redacted_url == "https://proxy-a.example:8443"
    assert first.url_hash.startswith("sha256:")
    assert first.secret_ref_hash is not None
    assert first.secret_ref_hash.startswith("sha256:")
    assert "vault:tenant-a/proxy-a" not in first.model_dump_json()
    assert second.secret_ref_hash is None

    # Деградация одного proxy исключает его из выдачи.
    rotator.mark_unhealthy("proxy-socks")
    assert rotator.healthy_count == 2
    after_unhealthy = [rotator.lease().proxy_id for _ in range(4)]
    assert "proxy-socks" not in after_unhealthy

    rotator.mark_healthy("proxy-socks")
    assert rotator.healthy_count == 3

    # Полная недоступность пула сигнализируется явной ошибкой.
    for proxy_id in ("proxy-http", "proxy-socks", "proxy-mtproto"):
        rotator.mark_unhealthy(proxy_id)
    with pytest.raises(TelegramProxyUnavailableError):
        rotator.lease()


def test_issue_71_proxy_rejects_inline_credentials_and_scheme_mismatch() -> None:
    with pytest.raises(ValidationError):
        TelegramProxyEndpoint(
            proxy_id="proxy-bad",
            protocol=TelegramProxyProtocol.HTTP,
            url="https://user:secret@proxy.example:8443",
        )

    with pytest.raises(ValidationError):
        TelegramProxyEndpoint(
            proxy_id="proxy-scheme",
            protocol=TelegramProxyProtocol.SOCKS5,
            url="https://proxy.example:8443",
        )

    with pytest.raises(TelegramProxyConfigurationError):
        TelegramProxyRotator(
            tenant_id="tenant-a",
            pool_id="pool-empty",
            endpoints=[],
        )


def test_issue_71_telegram_client_isolates_proxy_pools_per_tenant() -> None:
    asyncio.run(_run_issue_71_proxy_pool_isolation_scenario())


async def _run_issue_71_proxy_pool_isolation_scenario() -> None:
    directory = InMemoryTelegramProxyDirectory()
    directory.register(
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
    gateway = _build_gateway(proxy_directory=directory)
    await gateway.link_account(
        tenant_id="tenant-a",
        member_id="member-1",
        telegram_user_id=TENANT_A_TELEGRAM_ID,
        correlation_id="corr-link-a",
    )
    await gateway.link_account(
        tenant_id="tenant-b",
        member_id="member-1",
        telegram_user_id=TENANT_B_TELEGRAM_ID,
        correlation_id="corr-link-b",
    )

    with_proxy = await gateway.handle_update(
        TelegramInboundMessage(
            tenant_id="tenant-a",
            telegram_user_id=TENANT_A_TELEGRAM_ID,
            text="/status",
            correlation_id="corr-cmd-a",
        )
    )
    without_proxy = await gateway.handle_update(
        TelegramInboundMessage(
            tenant_id="tenant-b",
            telegram_user_id=TENANT_B_TELEGRAM_ID,
            text="/status",
            correlation_id="corr-cmd-b",
        )
    )

    assert with_proxy.proxy_lease is not None
    assert with_proxy.proxy_lease.proxy_id == "proxy-http"
    assert without_proxy.proxy_lease is None


def test_issue_71_parse_telegram_command_handles_bot_suffix_and_keywords() -> None:
    assert parse_telegram_command("/balance@nmc_bot").scenario is (
        TelegramClientScenario.BALANCE
    )
    assert parse_telegram_command("status").scenario is TelegramClientScenario.STATUS
    command = parse_telegram_command("/tasks открытые")
    assert command.scenario is TelegramClientScenario.TASKS
    assert command.argument == "открытые"
    assert parse_telegram_command("привет").scenario is (TelegramClientScenario.UNKNOWN)


def test_issue_71_telegram_client_docs_are_marked_implemented() -> None:
    spec = (ROOT / "docs/modules/messenger-adapter.md").read_text(encoding="utf-8")
    readme = (ROOT / "services/messenger-adapter/README.md").read_text(encoding="utf-8")
    security = (ROOT / "docs/SECURITY.md").read_text(encoding="utf-8")
    events = (ROOT / "docs/contracts/events.md").read_text(encoding="utf-8")

    for marker in (
        "#71",
        "TelegramClientGateway",
        "TelegramProxyRotator",
        "TelegramIdentityCipher",
        "messenger.telegram_client.account_linked",
        "messenger.telegram_client.command_handled",
        "telegram_user_ref_hash",
    ):
        assert marker in spec

    for marker in (
        "Telegram-клиент участника",
        "TelegramClientGateway",
        "TelegramProxyRotator",
    ):
        assert marker in readme

    assert "Telegram-клиент" in security
    assert "telegram_client_identity" in security
    assert "messenger.telegram_client.command_handled" in events


def _build_gateway(
    *,
    event_publisher: InMemoryEventBus | None = None,
    proxy_directory: InMemoryTelegramProxyDirectory | None = None,
) -> TelegramClientGateway:
    members = InMemoryTelegramMemberContextProvider()
    members.save(
        TelegramMemberSnapshot(
            tenant_id="tenant-a",
            member_id="member-1",
            status_label="Действительный участник",
            contribution_weight=1.75,
            points_balance=4242,
            open_task_titles=("Проверить материал", "Согласовать тему"),
        )
    )
    members.save(
        TelegramMemberSnapshot(
            tenant_id="tenant-b",
            member_id="member-1",
            status_label="Кандидат",
            contribution_weight=0.5,
            points_balance=999999,
            open_task_titles=(),
        )
    )
    members.save(
        TelegramMemberSnapshot(
            tenant_id="tenant-c",
            member_id="member-1",
            status_label="Кандидат",
            contribution_weight=0.5,
            points_balance=111111,
            open_task_titles=(),
        )
    )
    return TelegramClientGateway(
        identity_cipher=TelegramIdentityCipher(PlatformTokenCipher(_encryption_key())),
        member_provider=members,
        proxy_directory=proxy_directory,
        event_publisher=event_publisher or InMemoryEventBus(),
    )


def test_issue_71_telegram_proxy_health_enum_values() -> None:
    assert TelegramProxyHealth.HEALTHY.value == "healthy"
    assert TelegramProxyHealth.UNHEALTHY.value == "unhealthy"
    assert TelegramProxyHealth.DISABLED.value == "disabled"
