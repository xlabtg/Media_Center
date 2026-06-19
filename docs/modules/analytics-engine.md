# Analytics Engine

**Статус:** 🟢 реализовано для #61 · **Этап:** Этап 3 — Расширенные модули · **Компонент:** `component:analytics`

Расчёт KPI и агрегатов активности, контента и вовлечённости для дашбордов и контуров обратной связи.

## Зона ответственности
- Расчёт KPI пилота (участие, контент, вовлечённость, действия)
- Агрегации по тенанту и периодам
- Предоставление метрик для дашбордов и RL-KPI loop

## Основные интерфейсы
- **POST** `/analytics/events` — запись нормализованных KPI-событий tenant
- **GET** `/analytics/kpi?period=` — значения KPI за период
- **GET** `/analytics/aggregates?period=` — агрегаты по категориям
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
