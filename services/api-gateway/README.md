# API Gateway

**Статус:** каркас сервиса + shared gateway-core baseline для issue #19.

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

## Gateway-core baseline

- Внешняя цепочка middleware: `TenantContextASGIMiddleware` проверяет JWT и
  запрет tenant override, `RBACASGIMiddleware` применяет endpoint policy,
  `APIGatewayASGIMiddleware` маршрутизирует запрос к downstream-сервису.
- Маршруты описываются через `GatewayRoute`: публичный prefix вида
  `/contribution-ledger/...` проксируется в сервис с внутренним path без этого
  prefix.
- Gateway перезаписывает trusted headers для downstream: `X-Tenant-Id`,
  `X-Subject-Id`, `X-Actor-Roles`, `X-Correlation-Id`, `X-Service-Name`,
  `X-Forwarded-Prefix` и `X-Original-Path`.
- При переданном `s2s_auth` Gateway подписывает downstream-запрос
  `X-S2S-*`/service credentials заголовками для внутренних endpoint'ов.
- Локальный `InMemoryRateLimiter` реализует fixed-window лимиты по ключу
  `tenant_id + subject + service`; production-реализация должна заменить store
  на Redis или другой общий backend.
- Опциональный `resource_manager` подключает tenant-level admission control:
  `TenantResourcePlan` ограничивает request window, `concurrent_operations`,
  `storage_bytes` и `queue_depth`, а `InMemoryTenantResourceManager` даёт
  локальный контракт для CI. В production этот backend должен быть общим для
  всех replica Gateway/worker'ов.
- Превышение лимита возвращает `429 rate_limited` с `Retry-After` и
  `X-RateLimit-*` headers.

## Связанные документы

- [Спецификация модуля](../../docs/modules/api-gateway.md)
- [Мультитенантное масштабирование](../../docs/MULTITENANT_SCALING.md)
- [Синхронные контракты](../../docs/contracts/sync-api.md)
- [ADR-0003: tenant-изоляция](../../docs/adr/0003-tenant-isolation-by-design.md)
