# Neuro-Agent Orchestrator

**Статус:** реализован минимальный backend-контур Neuro-Agent Orchestrator для
in-memory сценариев этапа 3.

## Назначение

Neuro-Agent Orchestrator запускает ограниченные автономные AI-задачи для анализа
аудитории и базового вовлечения, не выходя за пороги Совета и compliance-gate по
ПДн.

## Реализованный слой

- Сбор аудитории допускает только открытые источники: `access_scope=public`,
  `tos_status=allowed`, непустое правовое основание и отсутствие
  `personal_data_fields`.
- Агрегированный `AudienceProfile` хранит reach, engagement rate, topic tags,
  legal basis и evidence hash без сырого handle, телефона, контактов или
  импортированных приватных данных.
- Авто-ответы исполняются только в пределах порогов Совета:
  `max_autonomous_risk_score`, `min_agent_confidence`,
  `max_autonomous_recipients` и allow-list шаблонов.
- Если риск, уверенность или размер аудитории выходят за пороги, запуск
  получает статус `needs_council_review`, а сообщение не считается
  отправленным.
- Все успешные профили, отправленные авто-ответы, эскалации и изменения
  порогов пишут hash-only audit record и публикуют tenant-scoped event.

## REST API

FastAPI-приложение создаётся через
`neuro_agent_orchestrator.create_neuro_agent_orchestrator_app` или entrypoint
`neuro_agent_orchestrator_app.main:app`.

- `POST /agents/run` запускает `audience_analysis` или
  `engagement_auto_reply`.
- `GET /agents/status?task_type=` возвращает историю запусков tenant.
- `GET /thresholds` возвращает действующие пороги Совета.
- `PUT /thresholds` обновляет пороги Совета и увеличивает `revision`.

Все рабочие endpoint требуют JWT tenant context. Запуск задач доступен ролям
`council`, `board`, `member_full`, `member_assoc`; чтение статусов доступно
`council`, `presidium`, `board`; изменение порогов доступно только `council`.

## Связанные документы

- [Спецификация модуля](../../docs/modules/neuro-agent-orchestrator.md)
- [Правовое соответствие](../../docs/COMPLIANCE.md)
- [Модель управления](../../docs/GOVERNANCE.md)
