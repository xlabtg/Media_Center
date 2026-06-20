# Acceptance snapshot этапа 5

Дата фиксации: 2026-06-20.

Статус: acceptance snapshot для issue #82.

Документ закрывает эпик [#82](https://github.com/xlabtg/Media_Center/issues/82)
как итоговую фиксацию готовности интеграционного контура. Он не заменяет
документы задач
[#75](https://github.com/xlabtg/Media_Center/issues/75),
[#76](https://github.com/xlabtg/Media_Center/issues/76),
[#77](https://github.com/xlabtg/Media_Center/issues/77),
[#78](https://github.com/xlabtg/Media_Center/issues/78),
[#79](https://github.com/xlabtg/Media_Center/issues/79),
[#80](https://github.com/xlabtg/Media_Center/issues/80) и
[#81](https://github.com/xlabtg/Media_Center/issues/81), а собирает их в один
проверяемый gate перед переходом к этапу 6.

## 1. Решение по этапу 5

Этап 5 считается завершенным как deterministic integration contour поверх
контрактов этапов 2-4:

- Telegram через Telethon публикует материалы, читает входящие обновления,
  хранит session string в AES-256-GCM и соблюдает tenant scoping;
- VK API выполняет `wall.post`, собирает reach/engagement через
  `stats.getPostReach` и `wall.getById`, применяет per-target rate limiting и
  не публикует access token в результатах;
- Dzen/OK и дополнительные площадки top-10 РФ подключены через
  platform-specific publishers и `RegistryHTTPPublisher`;
- РФ-платёжный шлюз исполняет HITL-выплату, синхронизирует статусы
  `accepted`/`succeeded`/`refunded` и не выводит суммы, recipient token или API
  key в audit/event payload;
- приватная блокчейн-сеть Hyperledger Besu 26.6.1/QBFT описана как локальный
  compose-профиль с четырьмя валидаторами, node permissioning, внутренним
  alias `besu-rpc` и мониторингом;
- реестр содержит 102 площадки с приоритетами, статусами, лимитами контента и
  tenant-scoped default targets;
- anti-blocking слой выдает proxy lease для внешних интеграций и переключает
  доставку на IPFS/TON/Matrix fallback channels без раскрытия proxy URL,
  `secret_ref` и endpoint.

Решение: можно переходить к этапу 6 и готовить pilot launch/production gates,
не меняя публичные REST/domain contracts. Реальные credentials, продуктивные
ПДн, публичные массовые публикации, реальные выплаты и открытие Besu RPC наружу
остаются запрещены до security/compliance approval и ограниченного пилота.

## 2. Трассировка задач #75-#81

| Issue | Результат | Основные артефакты |
|-------|-----------|--------------------|
| #75 | Telegram через Telethon поддерживает зашифрованные tenant-scoped сессии, публикацию с retry/flood wait и inbound bridge в Telegram Client Gateway. | [services/messenger-adapter/messenger_adapter/telegram_telethon.py](../services/messenger-adapter/messenger_adapter/telegram_telethon.py), [tests/test_telegram_telethon_issue75_acceptance_contract.py](../tests/test_telegram_telethon_issue75_acceptance_contract.py) |
| #76 | VK API публикует посты, собирает reach/engagement метрики и применяет rate limiting на target/action. | [services/messenger-adapter/messenger_adapter/vk_adapter.py](../services/messenger-adapter/messenger_adapter/vk_adapter.py), [tests/test_vk_api_issue76_acceptance_contract.py](../tests/test_vk_api_issue76_acceptance_contract.py) |
| #77 | Dzen/OK и дополнительные площадки top-10 РФ подключены через специализированные и registry-driven HTTP publishers. | [services/messenger-adapter/messenger_adapter/dzen_adapter.py](../services/messenger-adapter/messenger_adapter/dzen_adapter.py), [services/messenger-adapter/messenger_adapter/ok_adapter.py](../services/messenger-adapter/messenger_adapter/ok_adapter.py), [services/messenger-adapter/messenger_adapter/generic_http_publisher.py](../services/messenger-adapter/messenger_adapter/generic_http_publisher.py), [tests/test_messenger_top10_issue77_acceptance_contract.py](../tests/test_messenger_top10_issue77_acceptance_contract.py) |
| #78 | РФ-платёжный шлюз добавляет `RFPayoutGatewayConnector`, статусную сверку и sanitized audit/events для HITL Payout Gateway. | [services/hitl-payout-gateway/hitl_payout_gateway/rf_payment_gateway.py](../services/hitl-payout-gateway/hitl_payout_gateway/rf_payment_gateway.py), [tests/test_rf_payment_gateway_issue78_acceptance_contract.py](../tests/test_rf_payment_gateway_issue78_acceptance_contract.py) |
| #79 | Приватная блокчейн-сеть Besu/QBFT описана через compose, bootstrap/runtime scripts, Prometheus config и runbook. | [infra/blockchain/docker-compose.yml](../infra/blockchain/docker-compose.yml), [infra/blockchain/README.md](../infra/blockchain/README.md), [tests/test_private_blockchain_network_issue79_acceptance_contract.py](../tests/test_private_blockchain_network_issue79_acceptance_contract.py) |
| #80 | Реестр 102 площадки фиксирует приоритеты, статусы, лимиты и routing через `build_default_platform_registry`. | [services/messenger-adapter/messenger_adapter/platform_registry.py](../services/messenger-adapter/messenger_adapter/platform_registry.py), [tests/test_messenger_platform_registry_issue80_acceptance_contract.py](../tests/test_messenger_platform_registry_issue80_acceptance_contract.py) |
| #81 | Anti-blocking контур добавляет proxy pools, proxy lease metadata и IPFS/TON/Matrix legal fallback channels. | [services/messenger-adapter/messenger_adapter/resilience.py](../services/messenger-adapter/messenger_adapter/resilience.py), [tests/test_integration_resilience_issue81_acceptance_contract.py](../tests/test_integration_resilience_issue81_acceptance_contract.py) |
| #82 | Родительский epic покрыт сквозным stage-5 acceptance contract, который связывает публикации, РФ-выплату, audit-chain, реестр площадок и fallback policy. | [tests/test_stage5_acceptance_contract.py](../tests/test_stage5_acceptance_contract.py), [docs/STAGE_5_ACCEPTANCE.md](STAGE_5_ACCEPTANCE.md) |

## 3. Критерии завершения эпика #82

| Критерий issue #82 | Статус | Проверяемые ссылки |
|--------------------|--------|--------------------|
| Публикация работает минимум на 3 площадках | Выполнено: stage-5 acceptance публикует один материал через Unified Messenger Adapter на Telegram, VK, Dzen и OK. Telegram проходит через proxy lease и TON fallback после отказа основного канала, VK/Dzen/OK используют mock HTTP без реальных credentials. | [tests/test_stage5_acceptance_contract.py](../tests/test_stage5_acceptance_contract.py), [tests/test_telegram_telethon_issue75_acceptance_contract.py](../tests/test_telegram_telethon_issue75_acceptance_contract.py), [tests/test_vk_api_issue76_acceptance_contract.py](../tests/test_vk_api_issue76_acceptance_contract.py), [tests/test_messenger_top10_issue77_acceptance_contract.py](../tests/test_messenger_top10_issue77_acceptance_contract.py) |
| Тестовая выплата проходит через платёжный шлюз РФ | Выполнено: stage-5 acceptance ставит выплату в HITL-очередь, подтверждает её через 2FA, исполняет через `RFPayoutGatewayConnector`, синхронизирует статус `succeeded` и проверяет hash-only blockchain metadata. | [tests/test_stage5_acceptance_contract.py](../tests/test_stage5_acceptance_contract.py), [tests/test_rf_payment_gateway_issue78_acceptance_contract.py](../tests/test_rf_payment_gateway_issue78_acceptance_contract.py), [services/hitl-payout-gateway/hitl_payout_gateway/rf_payment_gateway.py](../services/hitl-payout-gateway/hitl_payout_gateway/rf_payment_gateway.py) |
| Развёрнута приватная блокчейн-сеть и работает прокси-ротация | Выполнено: stage-5 acceptance проверяет Besu/QBFT compose markers, chain id `20260679`, 4 валидатора, каталог 102 площадок, proxy lease и IPFS/TON fallback health state. | [tests/test_stage5_acceptance_contract.py](../tests/test_stage5_acceptance_contract.py), [tests/test_private_blockchain_network_issue79_acceptance_contract.py](../tests/test_private_blockchain_network_issue79_acceptance_contract.py), [tests/test_messenger_platform_registry_issue80_acceptance_contract.py](../tests/test_messenger_platform_registry_issue80_acceptance_contract.py), [tests/test_integration_resilience_issue81_acceptance_contract.py](../tests/test_integration_resilience_issue81_acceptance_contract.py) |

## 4. Gate перед этапом 6

Этап 6 может стартовать при следующих условиях:

- реальные площадочные credentials подключаются только через encrypted token
  stores/session stores, без коммитов секретов и без raw token в логах,
  событиях, audit records или PR-артефактах;
- production-публикации идут через platform registry, retry/rate limits, proxy
  lease policy и legal fallback routes, а не напрямую из бизнес-кода;
- любые реальные выплаты остаются в HITL-контуре с окном вето, 2FA Совета,
  idempotency key и hash-only blockchain audit;
- Besu RPC остаётся внутренним docker/network alias, внешние порты p2p/RPC не
  публикуются, а node permissioning и Prometheus alerts остаются обязательными;
- каталог 102 площадок можно расширять только через tenant-scoped registry
  entries с documented limits/status и compliance policy;
- суммы, recipient tokens, raw member ids, platform tokens, proxy URLs,
  fallback endpoints, raw content и ПДн не попадают в публичные payload.

## 5. Локальная проверка

Минимальный локальный acceptance для этапа 5:

```bash
pytest tests/test_stage5_acceptance_contract.py
```

Полная проверка integration contracts этапа 5:

```bash
pytest \
  tests/test_telegram_telethon_issue75_acceptance_contract.py \
  tests/test_vk_api_issue76_acceptance_contract.py \
  tests/test_messenger_top10_issue77_acceptance_contract.py \
  tests/test_rf_payment_gateway_issue78_acceptance_contract.py \
  tests/test_private_blockchain_network_issue79_acceptance_contract.py \
  tests/test_messenger_platform_registry_issue80_acceptance_contract.py \
  tests/test_integration_resilience_issue81_acceptance_contract.py \
  tests/test_stage5_acceptance_contract.py
```

Перед финальным ревью PR должен также проходить общий локальный CI:

```bash
ruff check .
ruff format --check .
black --check .
mypy .
pytest
```

## 6. Открытые ограничения

Этап 5 завершает проверяемый integration contour, но не является разрешением на
production launch. До этапов 6-7 ещё нужны реальные sandbox/prod credentials,
ручная проверка ToS площадок, legal/compliance approval по ПДн и выплатам,
incident runbooks, backup/restore drills для Besu, нагрузочные проверки,
наблюдаемость внешних каналов и ограниченный pilot tenant с rollback plan.
