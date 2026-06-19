# Activity Command Center

**Статус:** реализован минимальный backend-контур Activity Command Center для
in-memory сценариев этапа 3.

## Назначение

Activity Command Center даёт Совету и операционному управлению единый backend
для порогов автоматизации, очереди задач и трёх контуров обратной связи.

## Реализованный слой

- Пороги Совета применяются при постановке задач: если `risk_score` выше
  `max_autonomous_risk_score`, `agent_confidence` ниже `min_agent_confidence`
  или очередь контура достигла лимита, задача переводится в
  `needs_council_review`.
- Очередь задач tenant-scoped: задачи создаются через `POST /tasks`, доступны
  через `GET /tasks` и агрегируются в `GET /activity/overview`.
- Поддержаны операционный, стратегический и адаптивный контуры обратной связи:
  8 ч, 48 ч и 14 дней соответственно.
- Обновление порогов доступно только роли `council`, фиксируется audit hash и
  публикует событие `activity.thresholds.updated`.
- Создание задачи публикует событие `activity.task.created` и пишет audit record
  с hash-only ссылками на участников.

## REST API

FastAPI-приложение создаётся через
`activity_command_center.create_activity_command_center_app` или entrypoint
`activity_command_center_app.main:app`.

- `GET /activity/overview` возвращает сводку tenant: активные пороги,
  количество задач по статусам и статистику трёх feedback loop.
- `POST /tasks` создаёт задачу для агента или участника и применяет текущие
  пороги Совета.
- `GET /tasks?status=&feedback_loop=` возвращает очередь задач tenant.
- `GET /thresholds` возвращает текущие пороги tenant.
- `PUT /thresholds` обновляет пороги Совета и увеличивает `revision`.

Все рабочие endpoint требуют JWT tenant context. Обзор и чтение очереди доступны
ролям `council`, `presidium`, `board`; создание задач доступно `council`,
`board`, `member_full`, `member_assoc`; изменение порогов доступно только
`council`.

## Связанные документы

- [Спецификация модуля](../../docs/modules/activity-command-center.md)
- [Модель управления](../../docs/GOVERNANCE.md)
