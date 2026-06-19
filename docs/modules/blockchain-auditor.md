# Private Blockchain Auditor

**Статус:** 🟢 реализованы access_controller и batch_writer · **Этап:** Этап 2 — Ключевые микросервисы · **Компонент:** `component:blockchain-auditor`

Неизменяемый аудит ключевых событий в приватной блокчейн-сети: только SHA256-хэши и метаданные, доступ только для Совета.

## Зона ответственности
- Подключение к приватной сети Hyperledger Besu 26.6.1 (QBFT) через внутренний gRPC connector
- Детерминированная генерация SHA256-хэшей событий
- Пакетная запись (batch) хэшей для эффективности
- Контроль доступа (только Совет) и верификация записей

## Основные интерфейсы
- **POST** `/audit/record` — записать хэш события (batch-агрегация)
- **GET** `/audit/verify?hash=` — проверить соответствие события записи

## Реализовано в issue #49
- `hash_generator` формирует детерминированный SHA256 по canonical JSON
  (`sort_keys=True`) и возвращает canonical payload для проверки.
- `GrpcBlockchainAuditConnector` использует `BLOCKCHAIN_AUDITOR_URL` и gRPC
  transport protocol для записи/чтения hash-only audit records.
- До generated proto/stub используется `InMemoryGrpcBlockchainAuditTransport`
  в unit-тестах, чтобы зафиксировать контракт записи и чтения.
- Metadata перед записью проверяются на отсутствие ПДн, сумм, токенов, сырого
  контента, голоса и transcript.

## Реализовано в issue #50
- `BlockchainAuditAccessController` применяет deny-by-default RBAC: чтение,
  одиночная запись и batch-запись audit records доступны только роли Совета,
  то есть роль `council`.
- Access controller проверяет соответствие `tenant_id` команды и доверенного
  `TenantContext`; cross-tenant запись или чтение отклоняется до transport.
- `GrpcBlockchainAuditConnector.record_audit_hashes()` передаёт пачку хэшей в
  transport одним batch-вызовом после проверки metadata policy.
- `AuditBatchWriter` проверяет размер набора и использует batch API одним
  transport-вызовом без одиночных сетевых вызовов для каждого audit record.

## Модель данных (черновик)
- **audit_records** — `tenant_id`, `event_type`, `hash`, `metadata`, `block_ref`, `created_at`

## Зависимости
- Приватная блокчейн-сеть Hyperledger Besu 26.6.1/QBFT (`BLOCKCHAIN_AUDITOR_URL`), gRPC connector
- RBAC (роль Совета)

## Безопасность и мультитенантность
- В сеть пишутся **только** SHA256-хэши и метаданные — без сумм и ПДн
- Чтение, одиночная запись и batch-запись аудита доступны только роли Совета
- Все операции сравнивают `tenant_id` ресурса с проверенным `TenantContext`
- Хэш детерминирован (`sort_keys=True`) и верифицируем

## Связанные задачи (issue)
- [#49](https://github.com/xlabtg/Media_Center/issues/49) — Коннектор сети (Hyperledger Besu/QBFT, gRPC) + hash_generator (`type:feature`)
- [#50](https://github.com/xlabtg/Media_Center/issues/50) — access_controller (только Совет) + batch_writer (`type:feature`)
- [#51](https://github.com/xlabtg/Media_Center/issues/51) — API верификации записей + тесты (`type:feature`)
- [#52](https://github.com/xlabtg/Media_Center/issues/52) — 🔗 Private Blockchain Auditor (`type:epic`)
- [#79](https://github.com/xlabtg/Media_Center/issues/79) — Развёртывание приватной блокчейн-сети (`type:feature`)

## Связанные документы
- [SECURITY.md](../SECURITY.md)
- [GOVERNANCE.md](../GOVERNANCE.md)
- [ARCHITECTURE.md](../ARCHITECTURE.md)
- [ADR-0006](../adr/0006-technology-stack-and-versions.md)
- [Детальный план разработки](../DEVELOPMENT_PLAN.md)

---
<sub>Черновик спецификации. Детализируется на этапе проектирования соответствующего модуля. Сгенерировано `experiments/gen_module_docs.py`.</sub>
