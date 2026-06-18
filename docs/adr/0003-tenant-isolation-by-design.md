# ADR-0003: Сквозная tenant-изоляция по tenant_id

- **Статус:** Accepted
- **Дата:** 2026-06-18
- **Связанный issue:** [#5](https://github.com/xlabtg/Media_Center/issues/5)

## Контекст

НМЦ должен поддерживать несколько независимых tenant'ов. Даже если MVP
стартует с одним пилотным tenant, архитектура не должна позволять смешивать
данные участников, вклад, публикации, токены площадок, выплаты, audit hash,
логи и метрики между tenant'ами.

Требования FR-01 и NFR-01 фиксируют: межтенантный доступ возвращает `403
tenant_isolation_violation` и записывается в аудит.

## Решение

Принять `tenant_id` как обязательный архитектурный инвариант:

- источник истины — проверенный JWT на API Gateway;
- Gateway передаёт tenant context всем downstream-сервисам;
- тело запроса не может переопределить `tenant_id`;
- все таблицы, события, audit records, метрики, логи, Redis-ключи, RabbitMQ
  routing keys, ChromaDB namespaces и S3-префиксы содержат tenant context;
- отсутствие tenant context или несовпадение tenant ресурса и JWT приводит к
  отказу обработки.

## Последствия

- Все сервисные репозитории и middleware обязаны фильтровать данные по
  `tenant_id`.
- Тесты cross-tenant доступа становятся обязательной частью будущей приёмки
  сервисов и security checks.
- В логах и метриках `tenant_id` допустим как технический label, но ПДн и
  содержимое пользовательских данных не логируются.
- Импорт данных, фоновые задачи и события должны проходить через тот же tenant
  context, что и HTTP-запросы.

## Связанные документы

- [SECURITY.md](../SECURITY.md)
- [REQUIREMENTS.md](../REQUIREMENTS.md)
- [modules/tenant-isolation.md](../modules/tenant-isolation.md)
- [contracts/events.md](../contracts/events.md)
