# Neuro-Agent Orchestrator

**Статус:** реализован минимальный backend-контур Neuro-Agent Orchestrator для
in-memory сценариев этапа 3.

Дополнительно реализован полный backend-контур Neuro-Agent Orchestrator для issue #58:
аудитория, авто-ответы, контент-гигиена, аналитика и tenant-scoped ротация
прокси работают под порогами Совета, hash-only аудитом и событиями.

## Назначение

Neuro-Agent Orchestrator запускает ограниченные автономные AI-задачи для анализа
аудитории, базового вовлечения, контент-гигиены и оптимизации публикаций, не
выходя за пороги Совета и compliance-gate по ПДн.

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
- Контент-гигиена помечает небезопасные или некачественные материалы через
  `content_hygiene`, возвращая только `content_hash`, `author_ref_hash`, оценки,
  флаги и причины политики без сырого текста.
- Аналитика публикаций рассчитывает engagement, CTR и conversion rate по
  `publication_optimization`, определяет performance band и формирует
  рекомендации по улучшению публикации.
- Рекомендации не применяются автоматически: `auto_applied=false`, а рискованные
  или недостаточно уверенные предложения получают `needs_council_review`.
- Прокси-ротация поддерживает HTTP, SOCKS5 и MTProto через tenant-scoped
  прокси-пулы, round-robin выдачу живых прокси и health-check статусы.
- Неживые прокси исключаются из выдачи после проверки живости, а оставшиеся
  живые endpoint продолжают обслуживать lease-запросы.
- Прокси-пулы изолированы по tenant_id: одинаковый `pool_id` у разных tenant
  хранит независимый набор proxy endpoint и отдельный rotation cursor.
- Epic-контракт #58 покрывает сквозной сценарий аудитории, авто-ответов, контент-гигиены, аналитики и ротации прокси с hash-only аудитом и событиями.

## REST API

FastAPI-приложение создаётся через
`neuro_agent_orchestrator.create_neuro_agent_orchestrator_app` или entrypoint
`neuro_agent_orchestrator_app.main:app`.

- `POST /agents/run` запускает `audience_analysis`, `engagement_auto_reply`,
  `content_hygiene` или `publication_optimization`.
- `GET /agents/status?task_type=` возвращает историю запусков tenant.
- `GET /thresholds` возвращает действующие пороги Совета.
- `PUT /thresholds` обновляет пороги Совета и увеличивает `revision`.
- `PUT /proxy-pools/{pool_id}` создаёт или заменяет tenant-scoped пул прокси.
- `GET /proxy-pools/{pool_id}` возвращает состояние пула без секретов.
- `POST /proxy-pools/{pool_id}/lease` выдаёт следующий живой proxy endpoint.
- `POST /proxy-pools/{pool_id}/health-checks` фиксирует живость прокси.

Все рабочие endpoint требуют JWT tenant context. Запуск задач доступен ролям
`council`, `board`, `member_full`, `member_assoc`; чтение статусов доступно
`council`, `presidium`, `board`; изменение порогов доступно только `council`.

## Проверки

```bash
pytest tests/test_neuro_agent_orchestrator_epic_acceptance_contract.py
pytest tests/test_neuro_agent_orchestrator_issue55_acceptance_contract.py
pytest tests/test_neuro_agent_orchestrator_issue56_acceptance_contract.py
pytest tests/test_neuro_agent_orchestrator_issue57_acceptance_contract.py
```

## Связанные документы

- [Спецификация модуля](../../docs/modules/neuro-agent-orchestrator.md)
- [Правовое соответствие](../../docs/COMPLIANCE.md)
- [Модель управления](../../docs/GOVERNANCE.md)
