# Acceptance snapshot этапа 2

Дата фиксации: 2026-06-19.

Статус: acceptance snapshot для issue #53.

Документ закрывает эпик [#53](https://github.com/xlabtg/Media_Center/issues/53)
как итоговую фиксацию готовности ключевых микросервисов. Он не заменяет
документы эпиков
[#34](https://github.com/xlabtg/Media_Center/issues/34),
[#38](https://github.com/xlabtg/Media_Center/issues/38),
[#43](https://github.com/xlabtg/Media_Center/issues/43),
[#48](https://github.com/xlabtg/Media_Center/issues/48) и
[#52](https://github.com/xlabtg/Media_Center/issues/52), а собирает их в один
проверяемый gate перед переходом к этапу 3.

## 1. Решение по этапу 2

Этап 2 считается завершенным как in-memory REST/adapter контур пяти ключевых
микросервисов:

- Contribution Ledger начисляет баллы, считает Кв с потолком
  `COUNCIL_CAP_KV = 0.10`, формирует payout distribution snapshot и публикует
  audit-запросы;
- CGLR рендерит Jinja2-шаблоны, валидирует контент, строит L1/L2/L3
  реферальные ссылки и логирует генерацию как вклад;
- HITL Payout Gateway ставит выплаты в очередь, соблюдает окно вето Совета,
  требует 2FA перед исполнением и сохраняет audit hash решений;
- Unified Messenger Adapter публикует готовый контент через единый batch-фасад,
  применяет tenant platform registry, retry policy, encrypted tokens и
  platform-specific transforms;
- Private Blockchain Auditor принимает batch hash-only audit records, запрещает
  суммы/ПДн в metadata, ограничивает доступ ролью Совета и позволяет проверять
  записанные события.

Решение: можно переходить к этапу 3 и расширять контур AI/голоса/аналитики
поверх реализованных service contracts. Реальные внешние публикации,
платёжные операции, паевые взносы, промышленная обработка ПДн и pilot launch
остаются запрещены до compliance/security gates этапов 5-7.

## 2. Трассировка эпиков #34, #38, #43, #48, #52

| Issue | Результат | Основные артефакты |
|-------|-----------|--------------------|
| #34 | Contribution Ledger & Weight Engine реализует points calculator, weight engine, payout exporter, audit events и REST API. | [services/contribution-ledger/contribution_ledger/api.py](../services/contribution-ledger/contribution_ledger/api.py), [services/contribution-ledger/contribution_ledger/points_calculator.py](../services/contribution-ledger/contribution_ledger/points_calculator.py), [services/contribution-ledger/contribution_ledger/weight_engine.py](../services/contribution-ledger/contribution_ledger/weight_engine.py), [services/contribution-ledger/contribution_ledger/payout_exporter.py](../services/contribution-ledger/contribution_ledger/payout_exporter.py), [tests/test_contribution_ledger_api.py](../tests/test_contribution_ledger_api.py) |
| #38 | CGLR реализует sandboxed template rendering, L1/L2/L3 routing, contribution logging и REST API генерации. | [services/cglr/cglr/api.py](../services/cglr/cglr/api.py), [services/cglr/cglr/template_engine.py](../services/cglr/cglr/template_engine.py), [services/cglr/cglr/link_rotator.py](../services/cglr/cglr/link_rotator.py), [tests/test_cglr_epic_acceptance_contract.py](../tests/test_cglr_epic_acceptance_contract.py) |
| #43 | HITL Payout Gateway реализует очередь выплат, окно вето, 2FA confirmation, исполнение через коннекторы и REST API. | [services/hitl-payout-gateway/hitl_payout_gateway/api.py](../services/hitl-payout-gateway/hitl_payout_gateway/api.py), [services/hitl-payout-gateway/hitl_payout_gateway/queue_manager.py](../services/hitl-payout-gateway/hitl_payout_gateway/queue_manager.py), [services/hitl-payout-gateway/hitl_payout_gateway/veto_manager.py](../services/hitl-payout-gateway/hitl_payout_gateway/veto_manager.py), [services/hitl-payout-gateway/hitl_payout_gateway/execution_manager.py](../services/hitl-payout-gateway/hitl_payout_gateway/execution_manager.py), [tests/test_hitl_payout_epic_acceptance_contract.py](../tests/test_hitl_payout_epic_acceptance_contract.py) |
| #48 | Unified Messenger Adapter реализует base adapter, защищённое хранение площадочных credentials, platform registry, content transforms и batch-фасад публикации. | [services/messenger-adapter/messenger_adapter/base_adapter.py](../services/messenger-adapter/messenger_adapter/base_adapter.py), [services/messenger-adapter/messenger_adapter/unified_adapter.py](../services/messenger-adapter/messenger_adapter/unified_adapter.py), [services/messenger-adapter/messenger_adapter/platform_registry.py](../services/messenger-adapter/messenger_adapter/platform_registry.py), [tests/test_messenger_epic_acceptance_contract.py](../tests/test_messenger_epic_acceptance_contract.py) |
| #52 | Private Blockchain Auditor реализует hash generator, gRPC connector abstraction, batch writer, access controller и API записи/верификации. | [services/blockchain-auditor/blockchain_auditor/api.py](../services/blockchain-auditor/blockchain_auditor/api.py), [services/blockchain-auditor/blockchain_auditor/connector.py](../services/blockchain-auditor/blockchain_auditor/connector.py), [services/blockchain-auditor/blockchain_auditor/batch_writer.py](../services/blockchain-auditor/blockchain_auditor/batch_writer.py), [tests/test_blockchain_auditor_epic_acceptance_contract.py](../tests/test_blockchain_auditor_epic_acceptance_contract.py) |

## 3. Критерии завершения эпика #53

| Критерий issue #53 | Статус | Проверяемые ссылки |
|--------------------|--------|--------------------|
| Сценарий «учёт вклада → выплата с вето» проходит end-to-end | Выполнено: acceptance-тест регистрирует вклад, пересчитывает Кв, получает payout distribution, ставит выплату в HITL-очередь, накладывает вето Совета и проверяет запрет исполнения отменённой выплаты. | [tests/test_stage2_acceptance_contract.py](../tests/test_stage2_acceptance_contract.py), [tests/test_contribution_ledger_api.py](../tests/test_contribution_ledger_api.py), [tests/test_hitl_payout_epic_acceptance_contract.py](../tests/test_hitl_payout_epic_acceptance_contract.py) |
| Сценарий «генерация → публикация» проходит end-to-end | Выполнено: acceptance-тест генерирует контент через CGLR, получает контент с реферальными ссылками и публикует его через Unified Messenger Adapter на активные площадки tenant. | [tests/test_stage2_acceptance_contract.py](../tests/test_stage2_acceptance_contract.py), [tests/test_cglr_epic_acceptance_contract.py](../tests/test_cglr_epic_acceptance_contract.py), [tests/test_messenger_epic_acceptance_contract.py](../tests/test_messenger_epic_acceptance_contract.py) |
| Аудит-хэши ключевых событий пишутся в блокчейн | Выполнено: вклад, постановка выплаты в очередь и veto decision записываются через Private Blockchain Auditor batch API как hash-only records без `amount`, `member_id` и `payout_share` в metadata. | [tests/test_stage2_acceptance_contract.py](../tests/test_stage2_acceptance_contract.py), [tests/test_blockchain_auditor_epic_acceptance_contract.py](../tests/test_blockchain_auditor_epic_acceptance_contract.py), [services/blockchain-auditor/blockchain_auditor/connector.py](../services/blockchain-auditor/blockchain_auditor/connector.py) |
| Ключевая бизнес-логика покрыта тестами | Выполнено: формулы баллов и Кв, CGLR routing, HITL veto/2FA/execution, messenger publication и blockchain audit имеют unit/acceptance tests; родительский epic покрыт сквозным acceptance contract. | [tests/test_points_calculator.py](../tests/test_points_calculator.py), [tests/test_weight_engine.py](../tests/test_weight_engine.py), [tests/test_cglr_link_rotator.py](../tests/test_cglr_link_rotator.py), [tests/test_hitl_payout_queue_veto.py](../tests/test_hitl_payout_queue_veto.py), [tests/test_stage2_acceptance_contract.py](../tests/test_stage2_acceptance_contract.py) |

## 4. Gate перед этапом 3

Этап 3 может стартовать при следующих условиях:

- новые AI/voice/analytics сервисы используют существующие tenant-aware
  service contracts, shared error envelope и audit/event conventions;
- любые действия, которые меняют деньги, статусы, публичный контент,
  массовые рассылки или политики, остаются за HITL/RBAC gates;
- audit-chain продолжает принимать только SHA256-хэши и безопасные metadata,
  без сумм выплат, ПДн, токенов, сырого контента и transcript payload;
- внешние API площадок, платёжные шлюзы и реальные blockchain nodes
  подключаются через адаптеры/коннекторы, чтобы текущие in-memory
  acceptance-сценарии оставались быстрыми и детерминированными;
- production persistence для сервисов добавляется без изменения публичных REST
  схем и событийных контрактов, уже покрытых тестами этапа 2.

## 5. Локальная проверка

Минимальный локальный acceptance для этапа 2:

```bash
pytest tests/test_stage2_acceptance_contract.py
```

Полная проверка сервисных epic contracts:

```bash
pytest \
  tests/test_contribution_ledger_api.py \
  tests/test_payout_exporter.py \
  tests/test_weight_engine.py \
  tests/test_cglr_epic_acceptance_contract.py \
  tests/test_hitl_payout_epic_acceptance_contract.py \
  tests/test_messenger_epic_acceptance_contract.py \
  tests/test_blockchain_auditor_epic_acceptance_contract.py \
  tests/test_stage2_acceptance_contract.py
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

Этап 2 завершает функциональный in-memory контур ключевых микросервисов, но не
является разрешением на пилот. До этапов 5-7 ещё нужны production persistence,
интеграции с реальными площадками и платёжными шлюзами, security review,
юридические gates, incident runbook, platform policy registry и pre-pilot
проверки по ПДн/финансам/контенту.
