# Activity Command Center

**Статус:** 🟢 реализовано · **Этап:** Этап 3 — Расширенные модули · **Компонент:** `component:activity-center`

Backend панели Совета и администратора: управление порогами, очередями задач и контурами обратной связи.

## Зона ответственности
- Управление порогами и политиками Совета (через Policy Manager)
- Очереди задач для агентов и участников
- Три контура обратной связи: операционный (1–24 ч), стратегический (24–72 ч), адаптивный/RL (7–30 дн.)
- Агрегация состояния активности для панели Совета

## Основные интерфейсы
- **GET** `/activity/overview` — сводка активности тенанта
- **POST** `/tasks` — создать задачу в очереди
- **GET** `/tasks` — прочитать очередь задач tenant с фильтрами `status` и `feedback_loop`
- **GET/PUT** `/thresholds` — просмотр и изменение порогов Совета

## Реализация
- `ActivityCommandCenter` хранит tenant-scoped пороги и очередь задач в
  `InMemoryActivityRepository`.
- `create_activity_command_center_app` создаёт FastAPI-приложение с общим
  tenant middleware, RBAC и audit sink.
- Пороги Совета применяются при постановке задач: превышение риска, низкая
  уверенность агента или переполнение очереди переводят задачу в
  `needs_council_review`.
- Три контура обратной связи реализованы как `operational` (8 ч), `strategic`
  (48 ч) и `adaptive` (336 ч / 14 дней); значения находятся внутри окон
  1–24 ч, 24–72 ч и 7–30 дней.
- Обновления порогов публикуют `activity.thresholds.updated`, создание задач —
  `activity.task.created`.

## Модель данных
- **thresholds** — `tenant_id`, `revision`, `max_autonomous_risk_score`,
  `min_agent_confidence`, лимиты очередей контуров, `audit_hash`, `updated_at`
- **tasks** — `tenant_id`, `task_type`, `payload`, `status`, `assignee`,
  `agent_id`, `risk_score`, `agent_confidence`, `feedback_loop`,
  `policy_decision`, `policy_revision`, `due_at`, `created_at`

## Зависимости
- Policy Manager (пороги и политики)
- Analytics Engine (метрики контуров), Notification Gateway

## Безопасность и мультитенантность
- Изменение порогов и политик доступно только роли Совета
- Все изменения порогов фиксируются в аудите

## Связанные задачи (issue)
- [#54](https://github.com/xlabtg/Media_Center/issues/54) — Activity Command Center: пороги, очереди задач, контуры (`type:feature`, реализовано)
- [#63](https://github.com/xlabtg/Media_Center/issues/63) — Policy Manager: политики и пороги Совета (`type:feature`)
- [#68](https://github.com/xlabtg/Media_Center/issues/68) — Панель Совета (HITL): вето, пороги, подтверждения (`type:feature`)

## Связанные документы
- [GOVERNANCE.md](../GOVERNANCE.md)
- [ARCHITECTURE.md](../ARCHITECTURE.md)
- [Детальный план разработки](../DEVELOPMENT_PLAN.md)

---
<sub>Спецификация синхронизирована с реализацией Activity Command Center для #54.</sub>
