# Wallet Module

**Статус:** 🟢 реализовано для #60 · **Этап:** Этап 3 — Расширенные модули · **Компонент:** `component:wallet`

Внутренний учёт метрических средств ценности (МСЦ) и операций участника. МСЦ — внутренняя метрика, не криптовалюта.

## Зона ответственности
- Ведение баланса МСЦ и истории операций участника
- Связь с выплатами и долями распределения
- Изоляция по тенанту и аудит операций

## Основные интерфейсы
- **POST** `/wallet/operations` — записать МСЦ-операцию участника с
  `Idempotency-Key`, audit hash и ссылкой на payout distribution или payout
- **GET** `/wallet/balance` — баланс МСЦ участника
- **GET** `/wallet/operations` — история операций

## Модель данных
- **wallet_operations** — `tenant_id`, `member_id`, `amount_mcv`,
  `balance_after_mcv`, `type`, `ref_type`, `ref_id`, `period`,
  `distribution_hash`, `payout_share`, `metadata`, `audit_hash`,
  `idempotency_key`, `created_by`, `created_at`
- Индексы: `idx_wallet_operations_tenant_member_created`,
  `idx_wallet_operations_tenant_ref`, `idx_wallet_operations_audit_hash`
- Уникальность: `uq_wallet_operations_tenant_idempotency`

## Зависимости
- Contribution Ledger, HITL Payout Gateway

## Безопасность и мультитенантность
- Операции изолированы по `tenant_id` и аудируются
- МСЦ не является денежной суммой и не выводится в блокчейн как сумма
- Запись операций доступна ролям `council` и `board`; участник читает свой
  баланс, роли `council`, `presidium`, `board` могут читать историю tenant
- Event payload `wallet.operation_recorded` содержит `operation_id`,
  `member_hash`, reference и `audit_hash`, но не раскрывает `member_id`,
  `amount_mcv` или `balance_after_mcv`

## Реализация
- [services/wallet/wallet/api.py](../../services/wallet/wallet/api.py) —
  REST API, in-memory repository, idempotency и audit/event contract
- [services/wallet/README.md](../../services/wallet/README.md) — запуск и
  границы сервиса
- [infra/db/alembic/versions/wallet_operations_0004.py](../../infra/db/alembic/versions/wallet_operations_0004.py) —
  tenant-owned таблица `wallet_operations`
- [tests/test_wallet_api.py](../../tests/test_wallet_api.py) — API и
  tenant-isolation контракт #60

## Связанные задачи (issue)
- [#60](https://github.com/xlabtg/Media_Center/issues/60) — Wallet Module: учёт МСЦ и операций (`type:feature`)
- [#78](https://github.com/xlabtg/Media_Center/issues/78) — Платёжные шлюзы РФ (`type:feature`)

## Связанные документы
- [ECONOMICS.md](../ECONOMICS.md)
- [GLOSSARY.md](../GLOSSARY.md)
- [Детальный план разработки](../DEVELOPMENT_PLAN.md)

---
<sub>Спецификация синхронизирована с реализацией Wallet Module для issue #60.</sub>
