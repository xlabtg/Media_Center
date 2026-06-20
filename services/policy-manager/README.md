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
  окна вето HITL, RL-KPI precision, `rl_kpi.window_days`,
  `rl_kpi.require_council_approval`, `rl_kpi.min_effect_lift` и флаг XAI.
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

## RL-KPI политики #101

Для production-контура #101 Policy Manager задаёт минимальные guardrails:

- `rl_kpi.window_days` — разрешает только адаптивное окно 7-30 дней;
- `rl_kpi.require_council_approval` — требует ручное решение Совета перед
  применением оптимизаций;
- `rl_kpi.min_effect_lift` — требует измеримый эффект не ниже 0,02, иначе
  предложение эскалируется на пересмотр.

Эти политики применяются через `POST /policies/apply` к фактам
`window_days`, `has_council_approval` и `effect_lift`.

## Связанные документы

- [Спецификация модуля](../../docs/modules/policy-manager.md)
- [Модель управления](../../docs/GOVERNANCE.md)
