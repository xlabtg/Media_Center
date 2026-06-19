# Policy Manager

**Статус:** реализован централизованный in-memory контур Policy Manager для
политик и порогов Совета.

## Назначение

Policy Manager хранит tenant-scoped политики, версии порогов и этических правил,
которые применяют автоматизированные сервисы и агенты. На текущем этапе сервис
фиксирует REST-контракт, RBAC, аудит изменений и детерминированное применение
пороговых правил.

## Реализованный слой

- `PolicyManager` отдаёт актуальные политики tenant через
  `InMemoryPolicyRepository`.
- Дефолтный набор включает пороги автономного риска, уверенности агента,
  окна вето HITL, RL-KPI precision и флаг XAI.
- Обновление политики доступно только роли `council`, увеличивает `version`,
  пишет hash-only audit record и публикует событие `policy.updated`.
- `POST /policies/apply` применяет актуальные версии выбранных политик к
  фактам сервиса или агента и возвращает `allow`/`escalate` с причинами.
- История версии сохраняет default `version=1` и все последующие решения Совета.

## REST API

FastAPI-приложение создаётся через `policy_manager.create_policy_manager_app`
или entrypoint `policy_manager_app.main:app`.

- `GET /policies` возвращает актуальный набор политик tenant.
- `PUT /policies/{key}` создаёт новую версию политики решением Совета.
- `GET /policies/{key}/history` возвращает историю версий политики.
- `POST /policies/apply` применяет текущие политики к входным `facts`.

Все рабочие endpoint требуют JWT tenant context. Чтение и применение политик
доступны ролям `council`, `presidium`, `board`, `member_full`, `member_assoc`;
изменение политик доступно только роли `council`.

## Связанные документы

- [Спецификация модуля](../../docs/modules/policy-manager.md)
- [Модель управления](../../docs/GOVERNANCE.md)
