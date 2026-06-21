# База знаний эксплуатации НМЦ

Дата фиксации: 2026-06-20.

Статус: kb-ready для issue #102.

База знаний - единая точка входа для эксплуатационных вопросов tenant-команд.
Она связывает `docs/OPERATIONS_MANUAL.md`,
`docs/TENANT_TRAINING_PROGRAM.md`, SRE/DR runbook'и, governance материалы и
FAQ. Содержимое обновляется без ПДн, секретов, платежных реквизитов, raw
content и закрытых материалов.

## 1. Карта знаний

| Тема | Где читать | Когда использовать |
|------|------------|--------------------|
| Запуск tenant | [OPERATIONS_MANUAL.md](OPERATIONS_MANUAL.md), [TENANT_MARKETPLACE.md](TENANT_MARKETPLACE.md) | Заявка, moderation, provisioning, readiness checklist |
| Ежедневная эксплуатация | [OPERATIONS_MANUAL.md](OPERATIONS_MANUAL.md), [PILOT_SUPPORT_RUNBOOK.md](PILOT_SUPPORT_RUNBOOK.md) | Start-of-day checks, support queue, закрытие дня |
| P0/P1 и incident response | [SRE_RUNBOOK.md](SRE_RUNBOOK.md) | Tenant leak, security/privacy, SLO burn, публикационный backlog |
| Backup/DR | [DISASTER_RECOVERY.md](DISASTER_RECOVERY.md) | Backup failure, restore drill, RTO/RPO, tenant restore validation |
| Управление и HITL | [GOVERNANCE.md](GOVERNANCE.md), [COUNCIL_GUIDE.md](COUNCIL_GUIDE.md) | Вето, 2FA, quorum, политики, спорные решения |
| RL-KPI | [GOVERNANCE.md](GOVERNANCE.md), `docs/modules/analytics-engine.md`, `docs/modules/policy-manager.md` | Proposal, approval, effect measurement, rollback |
| Обучение | [TENANT_TRAINING_PROGRAM.md](TENANT_TRAINING_PROGRAM.md) | Track, knowledge check, контрольный сценарий, evidence |
| FAQ участников | [FAQ.md](FAQ.md), [USER_GUIDE.md](USER_GUIDE.md) | Частые вопросы участников, Совета и поддержки |

## 2. Быстрые ответы

### Как понять, что tenant готов к запуску?

Проверить readiness checklist в `docs/OPERATIONS_MANUAL.md`: moderation
пройдена, роли назначены, resource plan применен, `tenant_id` есть в логах и
evidence, SRE/DR контур готов, HITL включен, команда обучена.

### Где смотреть P0/P1?

P0/P1 классифицируются по [SRE_RUNBOOK.md](SRE_RUNBOOK.md) и support runbook.
Любой tenant leak, ПДн/секрет в evidence, обход HITL/2FA или потеря audit state
идет в security/privacy и Совет.

### Кто обновляет материалы после инцидента?

Incident owner пишет corrective action, SRE/on-call обновляет runbook,
knowledge-owner обновляет базу знаний, Совет подтверждает изменения, если они
затрагивают governance, HITL, RL-KPI или compliance gate.

### Как обучить новую tenant-команду?

Использовать `docs/TENANT_TRAINING_PROGRAM.md`, пройти нужные tracks,
контрольный сценарий и knowledge check. Evidence фиксируется только в
обезличенном формате.

### Когда обновлять FAQ?

Если вопрос повторился два раза за 14 дней, привел к ошибке поддержки,
изменился runbook или появился новый go/no-go gate.

## 3. Runbook update workflow

1. Найти источник изменения: incident, postmortem, training gap, новая
   процедура, повторяющийся вопрос или решение Совета.
2. Назначить владельца материала и affected documents.
3. Обновить основной runbook, затем базу знаний и связанные quick answers.
4. Проверить, что текст не содержит ПДн, секреты, платежные реквизиты, raw
   content и закрытые материалы.
5. Если меняется operational behavior, обновить training program и провести
   short knowledge check для затронутого track.
6. В PR указать issue, измененные документы и тест
   `tests/test_operations_training_issue102_acceptance_contract.py`.

## 4. Матрица владельцев

| Материал | Owner | Backup owner | Review trigger |
|----------|-------|--------------|----------------|
| `docs/OPERATIONS_MANUAL.md` | operations-lead | knowledge-owner | Изменение tenant lifecycle, RACI или readiness checklist |
| `docs/TENANT_TRAINING_PROGRAM.md` | knowledge-owner | operations-lead | Новый track, postmortem action, провал knowledge check |
| `docs/KNOWLEDGE_BASE.md` | knowledge-owner | support-lead | Повторяющийся вопрос или изменение runbook |
| `docs/SRE_RUNBOOK.md` | sre-lead | sre-oncall | P0/P1, alert routing, SLO/SLA change |
| `docs/DISASTER_RECOVERY.md` | sre-lead | backend-oncall | Restore drill, RTO/RPO или backup policy change |
| `docs/GOVERNANCE.md` | council-duty | board | HITL, quorum, RL-KPI или compliance gate |

## 5. Review cadence

База знаний проверяется каждые 14 дней или раньше при P0/P1, изменении
runbook'а, go/no-go решении Совета, изменении resource plan или повторяющемся
вопросе. Минимальный review:

- проверить ссылки на `docs/OPERATIONS_MANUAL.md` и
  `docs/TENANT_TRAINING_PROGRAM.md`;
- сверить owner и backup owner;
- удалить устаревшие quick answers;
- добавить новые вопросы из support queue;
- проверить, что нет ПДн, секретов и закрытого содержимого;
- обновить training gaps, если knowledge check показал проблему.

## 6. Проверка

```bash
pytest tests/test_operations_training_issue102_acceptance_contract.py
```
