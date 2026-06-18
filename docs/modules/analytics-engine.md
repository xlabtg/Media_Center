# Analytics Engine

**Статус:** 🟡 планируется · **Этап:** Этап 3 — Расширенные модули · **Компонент:** `component:analytics`

Расчёт KPI и агрегатов активности, контента и вовлечённости для дашбордов и контуров обратной связи.

## Зона ответственности
- Расчёт KPI пилота (участие, контент, вовлечённость, действия)
- Агрегации по тенанту и периодам
- Предоставление метрик для дашбордов и RL-KPI loop

## Основные интерфейсы
- **GET** `/analytics/kpi?period=` — значения KPI за период
- **GET** `/analytics/aggregates` — агрегаты по категориям

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
