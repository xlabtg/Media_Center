# Contribution Ledger & Weight Engine

**Статус:** базовый REST API сервиса для этапа 2.

## Назначение

Contribution Ledger & Weight Engine фиксирует вклад участников, начисляет
баллы, рассчитывает коэффициент влияния Кв и готовит данные для выплат под
контролем HITL Payout Gateway.

## Границы ответственности

- владеет таблицами `contributions`, `tenant_weights`,
  `payout_distributions`;
- рассчитывает баллы и Кв по правилам экономической модели;
- экспортирует распределение долей без прямого исполнения выплат;
- отправляет audit-запросы только через Private Blockchain Auditor.

## Реализованные модули

- `contribution_ledger.points_calculator` — расчёт `final_points` по формуле
  `round(base × platform_mult × reach_mult × amp_mult, 2)`, Pydantic v2
  модели входа/выхода и таблицы `BASE_POINTS` / `PLATFORM_MULTIPLIERS`.
- `contribution_ledger.weight_engine` — расчёт `kv_raw`, `kv_capped` с
  потолком `COUNCIL_CAP_KV = 0.10` и нормализованных `payout_share` для
  передачи в контур выплат.
- `contribution_ledger.payout_exporter` — формирование immutable snapshot
  распределения для HITL Payout Gateway, `distribution_hash` и событие
  `payout.distribution_ready`.
- `contribution_ledger.contribution_events` — генерация audit hash вклада через
  общий `AuditLogger` и публикация `contribution.recorded` /
  `audit.record.requested` через общий RabbitMQ event contract.
- `contribution_ledger.api` — FastAPI-приложение с Pydantic v2 схемами,
  tenant middleware, OpenAPI, регистрацией вкладов, пересчётом весов и выдачей
  payout distribution snapshot.

## REST API

Сервис создаётся через `create_contribution_ledger_app()` или entrypoint
`contribution_ledger_app.main:app` и использует общий `create_base_app()`
contract: `/health`, `/ready`, `/info`, `/metrics`, `/admin/log-level`, `/docs`,
`/openapi.json` доступны как единые runtime endpoints, а доменные операции
требуют Bearer JWT и проверенный tenant context.

Единый исполняемый запуск:

```bash
PYTHONPATH=services/contribution-ledger:. \
JWT_SECRET=local-jwt-secret \
python -m contribution_ledger_app.main
```

По умолчанию runner вызывает `uvicorn.run(app, host="0.0.0.0", port=7700)`.
`APP_HOST`, `APP_PORT`, `LOG_LEVEL`, `SERVICE_NAME` и `SERVICE_VERSION`
переопределяются через окружение.

| Метод | Путь | Назначение |
|-------|------|------------|
| `POST` | `/contributions` | Зарегистрировать вклад, начислить баллы и вернуть `audit_hash`. |
| `GET` | `/weights?period=YYYY-MM` | Получить `kv_raw`, `kv_capped` и `payout_share` по участникам tenant. |
| `POST` | `/weights/recalculate` | Пересчитать и сохранить snapshot весов за период. |
| `GET` | `/payout-distribution?period=YYYY-MM` | Получить immutable snapshot долей для HITL Payout Gateway. |

Для локальных unit/integration-тестов API использует in-memory repository и
`InMemoryEventBus`. Production persistence поверх `contributions`,
`tenant_weights` и `payout_distributions` подключается отдельным адаптером без
изменения публичных схем.

## Связанные документы

- [Спецификация модуля](../../docs/modules/contribution-ledger.md)
- [Экономическая модель](../../docs/ECONOMICS.md)
- [Модель данных](../../docs/DATA_MODEL.md)
