# Tenant Isolation Layer

**Статус:** 🟡 планируется · **Этап:** Этап 1 — Базовая инфраструктура и мультитенантность · **Компонент:** `component:tenant-core`

Сквозная изоляция тенантов на всех слоях: БД, кэш, очереди, векторная БД, объектное хранилище и логи.

## Зона ответственности
- Единые утилиты контекста тенанта (`tenant_id`) для всех сервисов
- Изоляция на уровне БД (фильтры/политики), ChromaDB (коллекции/namespace), S3/MinIO (префиксы), Redis (ключи), RabbitMQ (маршрутизация)
- Проверка PostgreSQL-таблиц и Alembic-миграций: tenant-owned таблицы обязаны
  иметь `tenant_id`, tenant-aware индексы и composite FK/unique rules
- Гарантия отсутствия межтенантных утечек данных

## Основные интерфейсы
- Библиотечный слой (middleware/зависимости FastAPI), не публичный REST
- Контракт: отсутствие `tenant_id` в контексте → отказ обработки

## Зависимости
- Общая библиотека `shared`, API Gateway, все слои хранения

## Безопасность и мультитенантность
- Любой межтенантный доступ → `403 tenant_isolation_violation`
- Тесты изоляции (cross-tenant → 403) обязательны на всех слоях
- 0 межтенантных утечек — критерий приёмки этапа 6

## Связанные задачи (issue)
- [#7](https://github.com/xlabtg/Media_Center/issues/7) — Проектирование модели данных и стратегии мультитенантности (`type:docs`)
- [#16](https://github.com/xlabtg/Media_Center/issues/16) — Tenant Isolation Layer: сквозная изоляция по tenant_id (`type:feature`)
- [#84](https://github.com/xlabtg/Media_Center/issues/84) — Тесты мультитенантной изоляции (cross-tenant → 403) (`type:test`)
- [#97](https://github.com/xlabtg/Media_Center/issues/97) — Мультитенантное масштабирование (`type:feature`)

## Связанные документы
- [SECURITY.md](../SECURITY.md)
- [ARCHITECTURE.md](../ARCHITECTURE.md)
- [DATA_MODEL.md](../DATA_MODEL.md)
- [ADR-0007](../adr/0007-data-model-and-tenant-storage.md)
- [COMPLIANCE.md](../COMPLIANCE.md)
- [Детальный план разработки](../DEVELOPMENT_PLAN.md)

---
<sub>Черновик спецификации. Детализируется на этапе проектирования соответствующего модуля. Сгенерировано `experiments/gen_module_docs.py`.</sub>
