# Private Blockchain Auditor

**Статус:** 🟡 планируется · **Этап:** Этап 2 — Ключевые микросервисы · **Компонент:** `component:blockchain-auditor`

Неизменяемый аудит ключевых событий в приватной блокчейн-сети: только SHA256-хэши и метаданные, доступ только для Совета.

## Зона ответственности
- Подключение к приватной сети Hyperledger Besu 26.6.1 (QBFT) через внутренний gRPC connector
- Детерминированная генерация SHA256-хэшей событий
- Пакетная запись (batch) хэшей для эффективности
- Контроль доступа (только Совет) и верификация записей

## Основные интерфейсы
- **POST** `/audit/record` — записать хэш события (batch-агрегация)
- **GET** `/audit/verify?hash=` — проверить соответствие события записи

## Модель данных (черновик)
- **audit_records** — `tenant_id`, `event_type`, `hash`, `metadata`, `block_ref`, `created_at`

## Зависимости
- Приватная блокчейн-сеть Hyperledger Besu 26.6.1/QBFT (`BLOCKCHAIN_AUDITOR_URL`), gRPC connector
- RBAC (роль Совета)

## Безопасность и мультитенантность
- В сеть пишутся **только** SHA256-хэши и метаданные — без сумм и ПДн
- Чтение и запись аудита доступны только роли Совета
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
