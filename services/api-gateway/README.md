# API Gateway

**Статус:** каркас сервиса, реализация запланирована в этапе 1.

## Назначение

API Gateway — единая внешняя точка входа платформы. Он отвечает за
tenant-aware маршрутизацию, проверку JWT/RBAC, rate limiting и передачу
проверенного tenant context downstream-сервисам.

## Границы ответственности

- принимает клиентские REST-запросы и проксирует их в сервисы;
- не позволяет внешним клиентам подменять `tenant_id`;
- нормализует error envelope и correlation headers;
- публикует события безопасности при нарушениях tenant-изоляции.

## Связанные документы

- [Спецификация модуля](../../docs/modules/api-gateway.md)
- [Синхронные контракты](../../docs/contracts/sync-api.md)
- [ADR-0003: tenant-изоляция](../../docs/adr/0003-tenant-isolation-by-design.md)
