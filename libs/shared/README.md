# Shared Library

**Статус:** базовый tenant-core слой для этапа 1.

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
