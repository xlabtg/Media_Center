# Acceptance snapshot этапа 1

Дата фиксации: 2026-06-18.

Статус: acceptance snapshot для issue #28.

Документ закрывает эпик [#28](https://github.com/xlabtg/Media_Center/issues/28)
как итоговую фиксацию готовности базовой инфраструктуры и мультитенантности. Он
не заменяет документы задач
[#16](https://github.com/xlabtg/Media_Center/issues/16)-[#27](https://github.com/xlabtg/Media_Center/issues/27),
а собирает их в один проверяемый gate перед переходом к этапу 2.

## 1. Решение по этапу 1

Этап 1 считается завершенным как общий технический фундамент для реализации
ключевых микросервисов:

- tenant context, tenant-aware ошибки, аудит нарушений изоляции и базовые
  репозитории вынесены в shared-библиотеку;
- JWT HS256, refresh rotation, TOTP-подтверждение чувствительных операций и
  RBAC-роли органов управления зафиксированы reusable API;
- API Gateway умеет маршрутизировать запросы по service prefix, пробрасывать
  проверенный tenant context и применять rate limiting;
- PostgreSQL/Alembic, Redis, RabbitMQ, ChromaDB и S3/MinIO имеют единые
  tenant-aware контракты и in-memory реализации для быстрых unit-тестов;
- observability baseline фиксирует Prometheus-метрики, структурные логи и
  traces с обязательным `tenant_id` и privacy guard;
- конфигурация и секреты описаны через `.env.example`, Pydantic Settings и
  Vault-compatible secret provider без реальных секретов в репозитории;
- `services/service-template/` поднимает новый FastAPI-сервис с healthcheck,
  `/metrics`, tenant middleware, DB settings, Alembic-структурой и smoke-test.

Решение: можно переходить к этапу 2 и начинать продуктовые сервисы поверх
зафиксированных shared/infra contracts. Реальные ПДн, паевые взносы, выплаты,
публичные интеграции и pilot launch остаются запрещены до pre-pilot gates из
[docs/COMPLIANCE.md](COMPLIANCE.md) и
[docs/RISK_REGISTER.md](RISK_REGISTER.md).

## 2. Трассировка задач #16-#27

| Issue | Результат | Основные артефакты |
|-------|-----------|--------------------|
| #16 | Tenant Isolation Layer извлекает `tenant_id` из JWT, держит request-scoped context, принудительно фильтрует tenant-owned ресурсы и аудирует cross-tenant отказ. | [libs/shared/tenant.py](../libs/shared/tenant.py), [tests/test_tenant_isolation_layer.py](../tests/test_tenant_isolation_layer.py), [docs/SECURITY.md](SECURITY.md) |
| #17 | Auth baseline выдаёт и проверяет JWT HS256, хранит refresh-токены только как SHA256-хэши, поддерживает rotation и TOTP для чувствительных операций. | [libs/shared/auth.py](../libs/shared/auth.py), [tests/test_auth_service.py](../tests/test_auth_service.py), [.env.example](../.env.example) |
| #18 | RBAC-модель ролей `council`, `presidium`, `board`, `member_full`, `member_assoc`, `audience` реализована как deny-by-default policy layer. | [libs/shared/rbac.py](../libs/shared/rbac.py), [tests/test_rbac_authorization.py](../tests/test_rbac_authorization.py), [docs/GOVERNANCE.md](GOVERNANCE.md) |
| #19 | API Gateway маршрутизирует по service prefix, пробрасывает доверенные tenant headers и ограничивает частоту запросов по tenant/service. | [libs/shared/gateway.py](../libs/shared/gateway.py), [tests/test_api_gateway_routing.py](../tests/test_api_gateway_routing.py), [docs/modules/api-gateway.md](modules/api-gateway.md) |
| #20 | Async DB слой фиксирует `DATABASE_URL=postgresql+asyncpg://...`, SQLAlchemy metadata, tenant models, repository contract и reversible Alembic migration. | [libs/shared/db.py](../libs/shared/db.py), [infra/db/alembic/versions/tenant_foundation_0001.py](../infra/db/alembic/versions/tenant_foundation_0001.py), [tests/test_db_layer.py](../tests/test_db_layer.py), [infra/db/README.md](../infra/db/README.md) |
| #21 | Redis cache и RabbitMQ event bus используют tenant-aware keys/routing, namespace invalidation, counters, locks и idempotent event processing. | [libs/shared/cache.py](../libs/shared/cache.py), [libs/shared/events.py](../libs/shared/events.py), [tests/test_cache_layer.py](../tests/test_cache_layer.py), [tests/test_event_bus.py](../tests/test_event_bus.py) |
| #22 | ChromaDB vector store разделяет коллекции и metadata по `tenant_id`, запрещает чужой tenant filter и покрыт in-memory контрактом. | [libs/shared/vector.py](../libs/shared/vector.py), [tests/test_vector_store.py](../tests/test_vector_store.py), [infra/local/docker-compose.yml](../infra/local/docker-compose.yml) |
| #23 | S3/MinIO object storage строит tenant/domain prefixes, metadata с SHA256 content hash, presigned URL и IAM-compatible prefix policy. | [libs/shared/object_storage.py](../libs/shared/object_storage.py), [tests/test_object_storage.py](../tests/test_object_storage.py), [infra/local/README.md](../infra/local/README.md) |
| #24 | Observability baseline добавляет tenant labels для метрик, JSON-логи, W3C trace context, OTLP collector, Prometheus rules и Grafana dashboard. | [libs/shared/observability.py](../libs/shared/observability.py), [infra/observability/README.md](../infra/observability/README.md), [tests/test_observability_contract.py](../tests/test_observability_contract.py) |
| #25 | Settings layer унифицирует `.env`, typed Pydantic Settings, secret providers, Vault KV v2 и redacted configuration snapshots. | [libs/shared/config.py](../libs/shared/config.py), [tests/test_config_settings.py](../tests/test_config_settings.py), [.env.example](../.env.example) |
| #26 | Shared foundation содержит Pydantic-модели, error envelope, audit hash logger и tenant helpers для Gateway/downstream wiring. | [libs/shared/service_template.py](../libs/shared/service_template.py), [libs/shared/models.py](../libs/shared/models.py), [libs/shared/audit_logger.py](../libs/shared/audit_logger.py), [libs/shared/errors.py](../libs/shared/errors.py), [tests/test_shared_foundation.py](../tests/test_shared_foundation.py) |
| #27 | FastAPI service template документирует копирование нового сервиса и уже содержит `/health`, `/metrics`, tenant-aware route, DB settings, Alembic и smoke-test. | [services/service-template/README.md](../services/service-template/README.md), [services/service-template/app/main.py](../services/service-template/app/main.py), [services/service-template/tests/test_health.py](../services/service-template/tests/test_health.py), [tests/test_service_template_scaffolding.py](../tests/test_service_template_scaffolding.py) |

## 3. Критерии завершения эпика #28

| Критерий issue #28 | Статус | Проверяемые ссылки |
|--------------------|--------|--------------------|
| Запрос с JWT проходит через Gateway с проверкой tenant_id | Выполнено: `TenantContextASGIMiddleware` валидирует Bearer JWT, создаёт `TenantContext`, а `APIGatewayASGIMiddleware` передает downstream только доверенные tenant headers. | [libs/shared/tenant.py](../libs/shared/tenant.py), [libs/shared/gateway.py](../libs/shared/gateway.py), [tests/test_api_gateway_routing.py](../tests/test_api_gateway_routing.py) |
| Межтенантный доступ возвращает 403 tenant_isolation_violation | Выполнено: tenant middleware, repository, DB, vector store, S3 storage и Gateway override checks возвращают единый error envelope и пишут sanitized audit event. | [libs/shared/errors.py](../libs/shared/errors.py), [tests/test_tenant_isolation_layer.py](../tests/test_tenant_isolation_layer.py), [tests/test_db_layer.py](../tests/test_db_layer.py), [tests/test_vector_store.py](../tests/test_vector_store.py), [tests/test_object_storage.py](../tests/test_object_storage.py) |
| Поднимается шаблон сервиса с метриками, миграциями и тестами | Выполнено: `create_service_app()` собирает FastAPI-приложение с `/health`, `/metrics`, tenant context route и DB settings; шаблон содержит Alembic-структуру и smoke-test. | [libs/shared/service_template.py](../libs/shared/service_template.py), [services/service-template/README.md](../services/service-template/README.md), [tests/test_service_template_scaffolding.py](../tests/test_service_template_scaffolding.py) |

## 4. Gate перед этапом 2

Этап 2 может стартовать при следующих условиях:

- новые сервисы создаются из [services/service-template/](../services/service-template/)
  или явно сохраняют тот же contract: `/health`, `/metrics`, tenant middleware,
  DB settings, Alembic и smoke-test;
- все API-запросы, SQL-запросы, cache keys, RabbitMQ routing keys, vector
  collections, S3 object keys, логи, метрики и traces используют проверенный
  `tenant_id`;
- доменные операции не принимают `tenant_id` из body/header как источник истины:
  источник истины остается JWT/Gateway context;
- каждый cross-tenant сценарий в новом сервисе покрывается негативным тестом на
  `403 tenant_isolation_violation` и sanitized audit event;
- денежные, управленческие, массовые и публичные действия проектируются через
  HITL/RBAC gates, включая `council`-доступ к blockchain audit и 2FA для
  выплат;
- секреты, токены, ПДн, сырое содержимое и суммы выплат не попадают в
  репозиторий, логи, метрики, traces, dashboards или audit-chain payload;
- внешние площадки, платёжные шлюзы и публичные публикации не включаются до
  соответствующих ToS/legal/compliance review в этапах 5-7.

## 5. Локальная проверка

Минимальный локальный acceptance для этапа 1:

```bash
python -m pip install -r requirements-dev.txt
ruff check .
ruff format --check .
black --check .
mypy .
pytest
```

Для точечной проверки stage-1 contracts:

```bash
pytest \
  tests/test_tenant_isolation_layer.py \
  tests/test_auth_service.py \
  tests/test_rbac_authorization.py \
  tests/test_api_gateway_routing.py \
  tests/test_db_layer.py \
  tests/test_cache_layer.py \
  tests/test_event_bus.py \
  tests/test_vector_store.py \
  tests/test_object_storage.py \
  tests/test_observability_contract.py \
  tests/test_config_settings.py \
  tests/test_shared_foundation.py \
  tests/test_service_template_scaffolding.py \
  tests/test_stage1_acceptance_contract.py
```

Для проверки локального infra baseline:

```bash
make test
```

## 6. Открытые ограничения

Этап 1 завершает reusable foundation, но не является готовностью к пилоту.
Продуктовые микросервисы этапа 2 ещё должны реализовать доменную логику,
персистентные сервисные миграции, e2e-сценарии и интеграционные проверки.
До этапов 5-7 запрещены реальные внешние публикации, обработка продуктивных
ПДн, платёжные операции, паевые взносы и публичный запуск tenant без отдельных
compliance, security и human-in-the-loop gates.
