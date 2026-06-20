# Analytics Engine

**Статус:** 🟢 реализовано для #61 и #92 · **Этап:** Этап 3/7 · **Компонент:** `component:analytics`

Расчёт KPI и агрегатов активности, контента и вовлечённости для дашбордов и контуров обратной связи.

## Зона ответственности
- Расчёт KPI пилота (участие, контент, вовлечённость, действия)
- Агрегации по тенанту и периодам
- Предоставление метрик для дашбордов и RL-KPI loop

## Основные интерфейсы
- **POST** `/analytics/events` — запись нормализованных KPI-событий tenant
- **GET** `/analytics/kpi?period=` — значения KPI за период
- **GET** `/analytics/aggregates?period=` — агрегаты по категориям
- **POST** `/analytics/pilot/telemetry/collect` — автоматический batch-сбор
  KPI, usage telemetry и incidents пилота
- **GET** `/analytics/pilot/reports?period=` — регулярный отчёт Совету по KPI,
  usage telemetry, incidents и feedback-loop статусу
- `build_analytics_kpi_response` и `build_analytics_aggregates_response` —
  публичные builder-функции для клиентского дашборда #69 без дублирования
  формул KPI вне Analytics Engine.

## Реализованный контракт #61
- `create_analytics_engine_app` собирает FastAPI-сервис Analytics Engine.
- `InMemoryAnalyticsRepository` хранит события для локальных тестов и ранней
  интеграции, фильтруя выборки через tenant-aware guard.
- KPI считаются по целевым метрикам пилота из `ROADMAP.md`: активные участники,
  новые участники, материалы, просмотры, среднее чтение, комментарии, задачи и
  инициативы.
- Агрегаты возвращаются по категориям `participation`, `content`,
  `engagement`, `actions`.
- Публичные builder-функции переиспользуются Web Cabinet для дашборда #69:
  JSON/HTML/CSV показывают те же KPI и агрегаты, что REST API Analytics Engine.
- Сырой `member_id` не публикуется в событиях Analytics Engine; для уникальных
  участников используется `member_hash`.
- tenant-isolation контракт #61: данные другого tenant не попадают в KPI и
  агрегаты, а подмена `X-Tenant-Id` возвращает `403 tenant_isolation_violation`.

## Реализованный контракт #92
- `POST /analytics/pilot/telemetry/collect` принимает tenant-scoped batch от
  pilot collector и автоматически превращает поле `kpi` в обычные
  `analytics.event_recorded` события для существующих KPI/aggregate builders.
- Usage telemetry и incidents сохраняются отдельно в `InMemoryAnalyticsRepository`
  и фильтруются тем же tenant-aware guard.
- Для batch пишется hash-only audit trail:
  `analytics.pilot_usage_recorded`, `analytics.pilot_incident_recorded` и
  `analytics.pilot_batch_collected`.
- `GET /analytics/pilot/reports?period=` доступен роли `council` и возвращает
  KPI, агрегаты, usage summary, incidents summary, weekly/monthly frequency,
  recipients `council` и `feedback_loop.status`.
- tenant-isolation контракт #92: KPI, usage telemetry и incidents другого tenant
  не попадают в отчёт Совету, а подмена `X-Tenant-Id` возвращает
  `403 tenant_isolation_violation`.

## Зависимости
- PostgreSQL, источники событий (вклад, публикации, действия)

## Безопасность и мультитенантность
- Все агрегаты и выборки изолированы по `tenant_id`

## Связанные задачи (issue)
- [#61](https://github.com/xlabtg/Media_Center/issues/61) — Analytics Engine: расчёт KPI и агрегаты (`type:feature`)
- [#69](https://github.com/xlabtg/Media_Center/issues/69) — Дашборды аналитики и KPI (`type:feature`)
- [#92](https://github.com/xlabtg/Media_Center/issues/92) — Сбор KPI и телеметрии пилота (`type:feature`)

## Связанные документы
- [ROADMAP.md](../ROADMAP.md)
- [ARCHITECTURE.md](../ARCHITECTURE.md)
- [Детальный план разработки](../DEVELOPMENT_PLAN.md)

---
<sub>Черновик спецификации. Детализируется на этапе проектирования соответствующего модуля. Сгенерировано `experiments/gen_module_docs.py`.</sub>
