# Policy Manager

**Статус:** 🟢 реализовано · **Этап:** Этап 3 — Расширенные модули · **Компонент:** `component:activity-center`

Централизованное управление политиками и порогами, применяемыми всеми автоматизированными модулями и агентами.

## Зона ответственности
- Хранение и версионирование политик и порогов Совета
- Предоставление актуальных политик сервисам и агентам
- Аудит изменений политик
- Конфигурация RL-KPI и этических правил

## Основные интерфейсы
- **GET** `/policies` — актуальные политики тенанта
- **PUT** `/policies/{key}` — изменить политику (роль Совета)
- **GET** `/policies/{key}/history` — история версий
- **POST** `/policies/apply` — применить актуальные политики к фактам сервиса или агента

## Реализация
- `PolicyManager` хранит tenant-scoped политики и историю версий в
  `InMemoryPolicyRepository`.
- `create_policy_manager_app` создаёт FastAPI-приложение с общим tenant
  middleware, RBAC, audit sink и health/metrics из service template.
- Дефолтный набор политик покрывает `automation.max_autonomous_risk_score`,
  `automation.min_agent_confidence`, `hitl.veto_window_hours`,
  `rl_kpi.min_precision`, `rl_kpi.window_days`,
  `rl_kpi.require_council_approval`, `rl_kpi.min_effect_lift` и
  `ethics.require_xai`.
- Обновление политики увеличивает `version`, фиксирует `audit_hash` и публикует
  событие `policy.updated` без ПДн.
- `POST /policies/apply` возвращает `allow` или `escalate`, версии применённых
  политик и причины нарушения порогов.

## RL-KPI guardrails #101
- `rl_kpi.window_days` фиксирует production-окно адаптивного контура 7-30 дней.
- `rl_kpi.require_council_approval` блокирует применение оптимизаций без
  решения Совета.
- `rl_kpi.min_effect_lift` требует измеримый uplift перед закреплением
  изменения и поддерживает rollback при деградации KPI.
- Эти правила применяются тем же `POST /policies/apply`, что и остальные
  tenant-scoped политики, поэтому сервисы получают единый `allow`/`escalate`
  контракт.

## Модель данных
- **policies** — `tenant_id`, `key`, `value`, `version`, `updated_by`,
  `updated_at`, `audit_hash`, `metadata`
- **policy_application** — входные `policy_keys` и `facts`, результат
  `decision`, `policy_versions`, `reasons`, `applied_at`

## Зависимости
- Activity Command Center, Neuro-Agent Orchestrator (потребители)

## Безопасность и мультитенантность
- Изменение политик доступно только роли Совета
- Все изменения политик версионируются и аудируются
- Чтение и применение политик выполняются только в пределах JWT tenant context;
  межтенантный доступ возвращает `403 tenant_isolation_violation`

## Связанные задачи (issue)
- [#54](https://github.com/xlabtg/Media_Center/issues/54) — Activity Command Center: пороги, очереди задач, контуры (`type:feature`)
- [#63](https://github.com/xlabtg/Media_Center/issues/63) — Policy Manager: политики и пороги Совета (`type:feature`, реализовано)
- [#68](https://github.com/xlabtg/Media_Center/issues/68) — Панель Совета (HITL): вето, пороги, подтверждения (`type:feature`)
- [#101](https://github.com/xlabtg/Media_Center/issues/101) — RL-KPI loop в проде (`type:feature`)

## Связанные документы
- [GOVERNANCE.md](../GOVERNANCE.md)
- [SECURITY.md](../SECURITY.md)
- [Детальный план разработки](../DEVELOPMENT_PLAN.md)

---
<sub>Спецификация синхронизирована с реализацией Policy Manager для #63.</sub>
