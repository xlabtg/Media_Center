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
