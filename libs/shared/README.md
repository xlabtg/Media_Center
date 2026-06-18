# Shared Library

**Статус:** базовый tenant/auth-core слой для этапа 1.

## Назначение

`libs/shared` будет содержать общий Python-код, который нужен нескольким
сервисам и не принадлежит одному домену. Библиотека не должна становиться
скрытым монолитом: доменная логика остаётся в соответствующих `services/*`.

## Реализовано для issue #16

- `TenantContext` и request-scoped context через `contextvars`;
- проверка JWT HS256 и извлечение `tenant_id`, `sub`, `roles`;
- ASGI middleware, совместимое с FastAPI/Starlette;
- единый error envelope для `403 tenant_isolation_violation` и `401 unauthorized`;
- `TenantScopedRepository` с обязательным `tenant_filter()` и проверкой владения
  ресурсом;
- sanitized audit event `tenant.isolation_violation` без ПДн и без сырого
  `requested_tenant_id`.

## Реализовано для issue #17

- `AuthTokenService` выдаёт JWT access-token HS256 с `tenant_id`, `sub`,
  `roles`, `iss`, `aud`, `iat`, `nbf`, `exp`, `jti` и `typ=access`;
- refresh-токены — opaque-значения: в store хранится только SHA256-хэш,
  исходный токен не логируется и не сохраняется;
- refresh rotation отзывает использованный токен и отклоняет повторное
  использование как `401 unauthorized`;
- `TOTPService` реализует RFC 6238 TOTP для чувствительных операций, включая
  `payout.confirm`;
- результат 2FA возвращается как `TwoFactorConfirmation` с `tenant_id`,
  `subject`, `operation`, `resource_id` и `correlation_id`.

## Реализовано для issue #18

- зафиксирован набор governance/RBAC ролей: `council`, `presidium`, `board`,
  `member_full`, `member_assoc`, `audience`;
- `AccessPolicy` и `require_access()` реализуют deny-by-default проверку ролей
  в пределах уже проверенного `TenantContext`;
- `ForbiddenError` возвращает единый error envelope с `403 forbidden`, не
  смешивая RBAC-отказы с `tenant_isolation_violation`;
- `RBACASGIMiddleware` даёт endpoint-level guard для API Gateway и сервисных
  ASGI/FastAPI приложений;
- `BLOCKCHAIN_AUDIT_ENDPOINT_POLICIES` ограничивает `/audit/record`,
  `/audit/verify` и `/audit/records/{event_id}` только ролью `council`.

## Реализовано для issue #19

- `GatewayRoute` описывает tenant-aware маршрут от публичного service prefix к
  downstream ASGI/FastAPI приложению;
- `APIGatewayASGIMiddleware` выбирает downstream по prefix, срезает gateway
  prefix из `scope["path"]` и передаёт проверенный tenant context через
  internal headers;
- downstream всегда получает `X-Tenant-Id`, `X-Subject-Id`, `X-Actor-Roles`,
  `X-Correlation-Id`, `X-Service-Name`, `X-Forwarded-Prefix` и
  `X-Original-Path` из доверенного Gateway context;
- `InMemoryRateLimiter` и `RateLimitPolicy` дают deterministic fixed-window
  limiter для локальной wiring и unit-тестов;
- превышение лимита возвращает единый error envelope `429 rate_limited` и
  headers `Retry-After`, `X-RateLimit-Limit`, `X-RateLimit-Remaining`,
  `X-RateLimit-Reset`.

## Реализовано для issue #20

- `DatabaseSettings`, `AsyncDatabase`, `create_async_engine_from_url()` и
  `create_async_session_factory()` задают единый async SQLAlchemy доступ через
  `DATABASE_URL=postgresql+asyncpg://...`;
- `Base.metadata` содержит Alembic/SQLAlchemy naming conventions из
  `DATA_MODEL.md`;
- `Tenant` и `TenantSetting` фиксируют первую tenant foundation модель;
- `TenantScopedSQLAlchemyRepository` строит обязательный `tenant_id` filter,
  выполняет async scalar-запросы и аудирует cross-tenant отказы через тот же
  `tenant.isolation_violation` contract;
- Alembic окружение и первая reversible migration находятся в `infra/db`.

## Реализовано для issue #21

- `CacheSettings`, `redis_url_from_env()` и `RedisTenantCache` задают единый
  Redis-backed слой кэша с pinned `REDIS_URL=redis://...`;
- `build_tenant_cache_key()` строит ключи вида
  `nmc:tenant:<tenant_id>:<namespace>:<key>`, чтобы кэш, счётчики и locks не
  пересекались между tenant;
- `InMemoryTenantCache` реализует тот же контракт для unit-тестов: JSON cache,
  namespace invalidation, counters и tenant-aware locks;
- `RabbitMQSettings`, `EventEnvelope`, `RabbitMQEventBus` и `InMemoryEventBus`
  фиксируют RabbitMQ топологию `nmc.events` / `nmc.commands` / `nmc.dlx`,
  tenant-aware routing key `tenant.<tenant_id>.<event_type>` и JSON envelope;
- `IdempotentEventProcessor` и `InMemoryEventIdempotencyStore` дают базовый
  inbox/idempotency contract: повторно доставленное успешно обработанное
  событие не запускает handler второй раз.

## Следующие области

- audit utilities для SHA256-хэшей и correlation metadata;
- Pydantic-модели, используемые в межсервисных контрактах;
- базовые helpers для конфигурации, логов и observability.

## Правила

1. Новый код попадает сюда только после проверки, что он нужен двум и более
   сервисам.
2. Shared API должен быть стабильнее внутренних API сервисов.
3. Любой helper для tenant или audit обязан сохранять инварианты из
   [SECURITY.md](../../docs/SECURITY.md) и [DATA_MODEL.md](../../docs/DATA_MODEL.md).
