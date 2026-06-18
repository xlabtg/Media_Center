# ADR-0007: Модель данных и tenant-aware стратегия хранения

- **Статус:** Accepted
- **Дата:** 2026-06-18
- **Связанный issue:** [#7](https://github.com/xlabtg/Media_Center/issues/7)

## Контекст

Issue #7 требует утвердить модель данных и стратегию мультитенантности до
начала реализации базовой инфраструктуры. Предыдущие решения уже зафиксировали
tenant isolation by design (ADR-0003), контракты взаимодействия и технологический
стек с PostgreSQL 17, SQLAlchemy async и Alembic.

Нужно снять неопределённость по:

- таблицам `contributions`, `tenant_weights` и связанным доменным данным;
- индексам и unique/FK правилам, которые предотвращают смешивание tenant'ов;
- изоляции в PostgreSQL, ChromaDB, S3 / MinIO, Redis, RabbitMQ, логах и
  метриках;
- плану миграций, который не позволит будущему коду обойти tenant contract.

## Решение

Принять [DATA_MODEL.md](../DATA_MODEL.md) как канонический baseline модели
данных и tenant-aware стратегии хранения.

Ключевые решения:

- `tenant_id` обязателен для всех tenant-owned таблиц, событий, Redis-ключей,
  RabbitMQ routing keys, ChromaDB collections, S3 / MinIO prefixes, логов,
  метрик и audit records.
- PostgreSQL-таблицы используют индексы и unique/FK constraints с `tenant_id`;
  Row Level Security добавляется как второй контур защиты после появления
  shared DB session context.
- Contribution Ledger фиксирует `contributions` и `tenant_weights` с явными
  индексами `idx_contributions_tenant_event_created` и
  `uq_tenant_weights_tenant_member_period`.
- ChromaDB изолируется коллекциями вида `nmc_<env>_<tenant_id>_<domain>` и
  дублирующим metadata filter `tenant_id`.
- S3 / MinIO изолируется prefix path
  `tenants/{tenant_id}/{domain}/{object_id}` и object metadata с tenant context.
- Alembic-миграции идут через expand/backfill/contract, используют naming
  conventions и не допускают tenant-owned таблицы без `tenant_id`.
- Любое отсутствие tenant context или несовпадение tenant ресурса и JWT
  возвращает `403 tenant_isolation_violation` и создаёт audit/security event.

## Последствия

- Будущие SQLAlchemy-модели, Alembic-ревизии и storage adapters должны
  соответствовать `DATA_MODEL.md`.
- Все новые доменные таблицы обязаны явно указать владельца данных, `tenant_id`,
  tenant-aware индексы и стратегию удаления/архивации.
- Миграционные PR должны проверять allowlist системных таблиц без `tenant_id` и
  включать тесты cross-tenant доступа.
- Добавление shared/system данных без tenant context допустимо только через
  отдельное архитектурное решение или явный allowlist.
- RLS повышает защиту, но не отменяет middleware и repository filters.

## Связанные документы

- [DATA_MODEL.md](../DATA_MODEL.md)
- [ARCHITECTURE.md](../ARCHITECTURE.md)
- [SECURITY.md](../SECURITY.md)
- [ADR-0003](0003-tenant-isolation-by-design.md)
- [ADR-0006](0006-technology-stack-and-versions.md)
- [contracts/events.md](../contracts/events.md)
