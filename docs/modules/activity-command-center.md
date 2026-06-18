# Activity Command Center

**Статус:** 🟡 планируется · **Этап:** Этап 3 — Расширенные модули · **Компонент:** `component:activity-center`

Backend панели Совета и администратора: управление порогами, очередями задач и контурами обратной связи.

## Зона ответственности
- Управление порогами и политиками Совета (через Policy Manager)
- Очереди задач для агентов и участников
- Три контура обратной связи: операционный (1–24 ч), стратегический (24–72 ч), адаптивный/RL (7–30 дн.)
- Агрегация состояния активности для панели Совета

## Основные интерфейсы
- **GET** `/activity/overview` — сводка активности тенанта
- **POST** `/tasks` — создать задачу в очереди
- **GET/PUT** `/thresholds` — просмотр и изменение порогов Совета

## Модель данных (черновик)
- **tasks** — `tenant_id`, `type`, `payload`, `status`, `assignee`, `created_at`

## Зависимости
- Policy Manager (пороги и политики)
- Analytics Engine (метрики контуров), Notification Gateway

## Безопасность и мультитенантность
- Изменение порогов и политик доступно только роли Совета
- Все изменения порогов фиксируются в аудите

## Связанные задачи (issue)
- [#54](https://github.com/xlabtg/Media_Center/issues/54) — Activity Command Center: пороги, очереди задач, контуры (`type:feature`)
- [#63](https://github.com/xlabtg/Media_Center/issues/63) — Policy Manager: политики и пороги Совета (`type:feature`)
- [#68](https://github.com/xlabtg/Media_Center/issues/68) — Панель Совета (HITL): вето, пороги, подтверждения (`type:feature`)

## Связанные документы
- [GOVERNANCE.md](../GOVERNANCE.md)
- [ARCHITECTURE.md](../ARCHITECTURE.md)
- [Детальный план разработки](../DEVELOPMENT_PLAN.md)

---
<sub>Черновик спецификации. Детализируется на этапе проектирования соответствующего модуля. Сгенерировано `experiments/gen_module_docs.py`.</sub>
