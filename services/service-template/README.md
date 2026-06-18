# FastAPI service template

**Статус:** эталонный шаблон микросервиса для issue #27.

## Назначение

`services/service-template/` показывает минимальную структуру нового backend
сервиса НМЦ. Его можно скопировать в `services/<service-name>/` и за несколько
минут получить FastAPI-приложение с единым shared wiring:

- `create_service_app` собирает приложение с `/health`, `/metrics` и примером
  tenant-aware route;
- `TenantContextASGIMiddleware` проверяет JWT и tenant headers для приватных
  endpoint'ов;
- `DatabaseSettings` валидирует `DATABASE_URL` и готовит точку подключения БД;
- `TenantMetricRegistry` отдаёт Prometheus-метрики
  `nmc_service_operations_total` и
  `nmc_service_operation_duration_seconds`;
- `alembic.ini` и `migrations/` дают стандартное место для сервисных миграций;
- `tests/test_health.py` фиксирует базовый smoke-test шаблона.

## Быстрый старт нового сервиса

1. Скопируйте каталог:

   ```bash
   cp -R services/service-template services/<service-name>
   ```

2. Замените `SERVICE_NAME` в `.env.example`, README и тестах на имя сервиса.
3. Добавьте доменные routes поверх `create_service_app` в `app/main.py`.
4. Создавайте миграции в `migrations/versions/`, сохраняя обязательный
   `tenant_id` для tenant-owned таблиц.
5. Запустите локальные проверки:

   ```bash
   ruff check .
   ruff format --check .
   mypy .
   pytest
   ```

## Runtime

```bash
export $(grep -v '^#' services/service-template/.env.example | xargs)
PYTHONPATH=. uvicorn --app-dir services/service-template app.main:app \
  --host "$APP_HOST" --port "$APP_PORT"
```

Для production-сборки `JWT_SECRET` должен приходить из secret manager или
окружения деплоя, а не из репозитория; значение в `.env.example` предназначено
только для локального smoke-test. Публичными остаются только `/health`,
`/metrics`, `/docs`, `/openapi.json` и `/redoc`; остальные маршруты требуют
валидный Bearer JWT и `X-Tenant-Id`.
