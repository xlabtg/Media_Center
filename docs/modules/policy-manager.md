# Policy Manager

**Статус:** 🟡 планируется · **Этап:** Этап 3 — Расширенные модули · **Компонент:** `component:activity-center`

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

## Модель данных (черновик)
- **policies** — `tenant_id`, `key`, `value`, `version`, `updated_by`, `updated_at`

## Зависимости
- Activity Command Center, Neuro-Agent Orchestrator (потребители)

## Безопасность и мультитенантность
- Изменение политик доступно только роли Совета
- Все изменения политик версионируются и аудируются

## Связанные задачи (issue)
- [#54](https://github.com/xlabtg/Media_Center/issues/54) — Activity Command Center: пороги, очереди задач, контуры (`type:feature`)
- [#63](https://github.com/xlabtg/Media_Center/issues/63) — Policy Manager: политики и пороги Совета (`type:feature`)
- [#68](https://github.com/xlabtg/Media_Center/issues/68) — Панель Совета (HITL): вето, пороги, подтверждения (`type:feature`)

## Связанные документы
- [GOVERNANCE.md](../GOVERNANCE.md)
- [SECURITY.md](../SECURITY.md)
- [Детальный план разработки](../DEVELOPMENT_PLAN.md)

---
<sub>Черновик спецификации. Детализируется на этапе проектирования соответствующего модуля. Сгенерировано `experiments/gen_module_docs.py`.</sub>
