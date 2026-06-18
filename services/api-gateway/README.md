# API Gateway

**Статус:** каркас сервиса + auth-core baseline для issue #17.

## Назначение

API Gateway — единая внешняя точка входа платформы. Он отвечает за
tenant-aware маршрутизацию, проверку JWT/RBAC, rate limiting и передачу
проверенного tenant context downstream-сервисам.

## Границы ответственности

- принимает клиентские REST-запросы и проксирует их в сервисы;
- выдаёт JWT access-token HS256 и вращаемые refresh-токены через auth boundary;
- проверяет 2FA/TOTP для чувствительных операций перед передачей в HITL;
- не позволяет внешним клиентам подменять `tenant_id`;
- нормализует error envelope и correlation headers;
- публикует события безопасности при нарушениях tenant-изоляции.

## Auth baseline

- Access JWT содержит `tenant_id`, `sub`, `roles`, `iss`, `aud`, `iat`, `nbf`,
  `exp`, `jti` и `typ=access`.
- Refresh-токен является opaque-значением: хранится только SHA256-хэш, при
  обновлении старый токен отзывается.
- TOTP используется для операций вроде `payout.confirm`; неверный или
  просроченный код возвращает `401 unauthorized`.
- `JWT_SECRET`, параметры TTL и TOTP issuer берутся из окружения или vault, а не
  из репозитория.

## Связанные документы

- [Спецификация модуля](../../docs/modules/api-gateway.md)
- [Синхронные контракты](../../docs/contracts/sync-api.md)
- [ADR-0003: tenant-изоляция](../../docs/adr/0003-tenant-isolation-by-design.md)
