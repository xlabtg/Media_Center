# Мультитенантное масштабирование

Статус: baseline для issue #97, этап 8 — масштабирование и эксплуатация.

Документ фиксирует минимальный контракт масштабирования платформы на несколько
tenant'ов без деградации изоляции. Базовая реализация находится в
`libs.shared.tenant_resources` и применима к API Gateway, очередям, объектному
хранилищу и локальным тестовым контурам.

Связанный baseline для issue #100 описан в [TENANT_MARKETPLACE.md](TENANT_MARKETPLACE.md):
самостоятельная заявка tenant проходит moderation, после `approve` создаёт
публичный профиль каталога и передаёт выбранный `resource_plan` в
`InMemoryTenantResourceManager.configure_tenant()`.

## Цели #97

| Критерий | Контракт |
|----------|----------|
| Платформа держит несколько tenant'ов | `InMemoryTenantResourceManager` хранит независимое состояние по `tenant_id`; request window, concurrency, storage и queue counters не смешиваются между tenant'ами. |
| Изоляция сохраняется под нагрузкой | `tests/test_multitenant_scaling_issue97_contract.py` запускает параллельный threaded-сценарий на двух tenant'ах и проверяет tenant-scoped выборку через `TenantScopedRepository`. |
| Ресурсы управляются по tenant'ам | `TenantResourcePlan` задаёт `request_limit`, `window_seconds`, `concurrent_operations`, `storage_bytes` и `queue_depth`; превышение лимита возвращает admission decision с причиной отказа. |

## Resource Plan

`TenantResourcePlan` описывает один тариф/профиль мощности:

- `request_limit` и `window_seconds` — fixed-window лимит входящих запросов;
- `concurrent_operations` — максимум одновременных операций tenant;
- `storage_bytes` — tenant quota для объектного/векторного хранилища или другого
  измеримого storage-сегмента;
- `queue_depth` — максимальная глубина tenant-scoped очереди.

План по умолчанию применяется ко всем tenant'ам, для которых не задан
индивидуальный профиль. Индивидуальные профили задаются через
`configure_tenant(tenant_id, plan)`.

## Admission Flow

1. API Gateway извлекает проверенный `TenantContext` из JWT.
2. `APIGatewayASGIMiddleware` выполняет существующий per subject/service rate
   limit.
3. Если настроен `resource_manager`, Gateway вызывает
   `admit_request(context, service_name, operation)`.
4. Перед downstream-вызовом Gateway резервирует `concurrent_operations` через
   `acquire_operation_slot`.
5. После downstream-вызова слот освобождается через `release_operation_slot`.
6. Storage и queue слои используют `reserve_storage_bytes` и
   `reserve_queue_items` перед записью или постановкой работы в очередь.

Отказы являются tenant-local: превышение лимита tenant A не меняет counters и
доступность tenant B. Для Gateway отказ конвертируется в `429 rate_limited` с
деталями `reason`, `plan`, `resource_type` и `retry_after_seconds`.

## Горизонтальное Масштабирование

In-memory реализация предназначена для unit/contract тестов и локального wiring.
Production backend должен заменить store на общий Redis/PostgreSQL backend с
атомарными операциями:

- request window — Redis `INCR` + TTL или SQL row с advisory lock;
- concurrency slots — Redis semaphore или lease table с TTL/reaper;
- storage quota — агрегаты из object/vector metadata и атомарный compare/update;
- queue depth — broker metrics или tenant-scoped pending counters.

Ключи и строки backend обязаны включать `tenant_id`, чтобы горизонтально
масштабируемые replica'и API Gateway и worker'ов видели единые лимиты tenant.

## Каталог и onboarding tenant'ов

Tenant Marketplace #100 добавляет controlled entrypoint для новых кооперативов:

- публичный каталог читает только опубликованные `tenant_marketplace_profiles`;
- `tenant_onboarding_applications` остаются вне каталога до решения модератора;
- модерация выполняется ролями `council`, `presidium` или `board`;
- `approve` создаёт tenant-профиль и применяет `TenantResourcePlan`;
- `request_changes` и `reject` не меняют resource counters и не создают
  `tenant_id`.

Такой flow сохраняет tenant-local отказоустойчивость: новый tenant получает
изолированный план ресурсов только после ручной проверки профиля, контактов,
политики данных и ожидаемого размера кооператива.

## Проверка

Быстрый контракт issue #97:

```bash
pytest tests/test_multitenant_scaling_issue97_contract.py
```

Связанный Gateway-контракт:

```bash
pytest tests/test_api_gateway_routing.py
```

Полный локальный gate перед PR остаётся тем же:

```bash
ruff check .
ruff format --check .
black --check .
mypy .
pytest
```
