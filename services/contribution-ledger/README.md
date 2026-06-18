# Contribution Ledger & Weight Engine

**Статус:** каркас сервиса, реализация запланирована в этапе 2.

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

## Связанные документы

- [Спецификация модуля](../../docs/modules/contribution-ledger.md)
- [Экономическая модель](../../docs/ECONOMICS.md)
- [Модель данных](../../docs/DATA_MODEL.md)
