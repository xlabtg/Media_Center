# Эксплуатационная документация НМЦ

Дата фиксации: 2026-06-20.

Статус: ops-ready для issue #102.

Документ собирает единый эксплуатационный пакет этап 8 для команд tenant'ов,
Правления, Совета, поддержки и SRE/on-call. Он не заменяет специализированные
runbook'и, а связывает их в ежедневный порядок работы: tenant lifecycle,
операционный день, RACI, readiness checklist, evidence policy и точки
эскалации.

Связанные документы:
[SRE_RUNBOOK.md](SRE_RUNBOOK.md),
[DISASTER_RECOVERY.md](DISASTER_RECOVERY.md),
[MULTITENANT_SCALING.md](MULTITENANT_SCALING.md),
[TENANT_MARKETPLACE.md](TENANT_MARKETPLACE.md),
[TENANT_TRAINING_PROGRAM.md](TENANT_TRAINING_PROGRAM.md),
[KNOWLEDGE_BASE.md](KNOWLEDGE_BASE.md),
[GOVERNANCE.md](GOVERNANCE.md),
[PILOT_RETROSPECTIVE_SCALE_PLAN.md](PILOT_RETROSPECTIVE_SCALE_PLAN.md).

Контракт готовности проверяется тестом
`tests/test_operations_training_issue102_acceptance_contract.py`.

## 1. Критерии приемки #102

| Критерий | Как выполняется | Проверка |
|----------|-----------------|----------|
| Документация эксплуатации полна и актуальна | Этот документ связывает stage-8 runbook'и по масштабированию, SRE, backup/DR, каталогу tenant'ов, RL-KPI и поддержке. | `docs/OPERATIONS_MANUAL.md`, README navigation |
| Проведено обучение команд tenant'ов | Программа обучения опубликована, а структурированное evidence зафиксировано без ПДн. | `docs/TENANT_TRAINING_PROGRAM.md`, `docs/operations/tenant-training-record.json` |
| База знаний доступна | База знаний содержит карту материалов, быстрые ответы, владельцев и цикл обновления. | `docs/KNOWLEDGE_BASE.md` |

## 2. Операционная модель

Production-ready эксплуатация начинается только после отдельного go/no-go
Совета и legal/security review. До этого все записи в репозитории остаются
planning-stage или synthetic evidence без реальных ПДн, секретов, платежных
реквизитов, сумм выплат, raw content и закрытых материалов.

Минимальные принципы эксплуатации:

- каждый запрос, лог, метрика, audit record и backup artifact сохраняет
  корректный `tenant_id`;
- evidence ведется по политике `no_pdn_no_secrets`;
- чувствительные действия проходят HITL, 2FA, окно вето и quorum rules;
- P0/P1 incidents закрываются через SRE flow, postmortem и корректирующие
  действия;
- RL-KPI работает только как supervised контур: предложение, решение Совета,
  измерение эффекта и rollback при деградации;
- обновления документации фиксируются в базе знаний и проверяются при ревью.

## 3. RACI

| Область | Responsible | Accountable | Consulted | Informed |
|---------|-------------|-------------|-----------|----------|
| Tenant onboarding и ресурсный план | tenant-admin / board | operations-lead | council, SRE | support |
| Каталог tenant'ов и moderation | board | council-duty | compliance, security | applicant tenant |
| Ежедневная эксплуатация | tenant-admin | operations-lead | support, SRE | council |
| P0/P1 incident response | sre-oncall | sre-lead | security-privacy, council | affected tenant |
| Backup/DR и restore drill | sre-oncall | sre-lead | security, backend | council |
| HITL, вето, политики, RL-KPI | council-duty | council | board, analytics, policy owner | tenant admins |
| База знаний и обучение | knowledge-owner | operations-lead | SRE, support, council | all tenant teams |

## 4. tenant lifecycle

1. Заявка: tenant отправляет профиль через сценарий из
   [TENANT_MARKETPLACE.md](TENANT_MARKETPLACE.md).
2. Moderation: Совет, Правление или Президиум проверяют профиль, контакты,
   data policy и readiness checklist.
3. Provisioning: после `approve` создается tenant profile, применяется
   `TenantResourcePlan` и настраиваются tenant-local limits.
4. Bootstrap: tenant-admin назначает роли, каналы поддержки, SRE contact,
   backup policy и knowledge base entrypoint.
5. Обучение: команды проходят программу из
   [TENANT_TRAINING_PROGRAM.md](TENANT_TRAINING_PROGRAM.md), evidence пишется в
   `docs/operations/tenant-training-record.json`.
6. Операционная эксплуатация: tenant работает по ежедневному циклу ниже,
   support triage и SRE runbook'ам.
7. Ретроспектива: Совет проверяет KPI, incidents summary, вопросы базы знаний и
   предложения RL-KPI.
8. Offboarding или suspension: доступы, публикации, очереди, backup и audit
   history закрываются без удаления неизменяемого аудита и без смешивания
   tenant data.

## 5. операционный день

### 5.1. Старт дня

- Проверить Alertmanager, Prometheus, Grafana и OpenTelemetry status.
- Проверить P0/P1 incidents, tenant isolation signals и support queue.
- Проверить backup jobs, restore-drill alerts и превышения RTO/RPO.
- Проверить HITL queue: выплаты, политики, массовые публикации, RL-KPI
  предложения.
- Сверить tenant resource limits: request window, concurrency, storage и queue.

### 5.2. В течение дня

- Tenant-admin обрабатывает onboarding, роли, каналы и вопросы базы знаний.
- Support triage назначает P0-P3, владельца, workaround и release gate.
- SRE/on-call ведет incidents по [SRE_RUNBOOK.md](SRE_RUNBOOK.md).
- Совет принимает veto/approve/reject решения для чувствительных операций.
- Knowledge-owner отмечает повторяющиеся вопросы для обновления FAQ/runbook'ов.

### 5.3. Закрытие дня

- Проверить, что нет незакрытых P0/P1 без владельца и mitigation.
- Сверить error budget и tenant-local degradations.
- Проверить, что backup evidence и incident evidence не содержат ПДн/секретов.
- Обновить базу знаний при повторяющемся вопросе или изменении процедуры.
- Передать спорные governance, HITL и RL-KPI вопросы в Совет.

## 6. Runbook map

| Сценарий | Основной документ | Когда открывать |
|----------|-------------------|-----------------|
| Масштабирование tenant'ов, квоты, лимиты | [MULTITENANT_SCALING.md](MULTITENANT_SCALING.md) | Новый tenant, изменение resource plan, tenant-local отказ |
| Каталог и самостоятельное подключение | [TENANT_MARKETPLACE.md](TENANT_MARKETPLACE.md) | Заявка, moderation, публикация профиля |
| SRE incident response | [SRE_RUNBOOK.md](SRE_RUNBOOK.md) | P0-P3 incident, error budget burn, alert routing |
| Backup/DR | [DISASTER_RECOVERY.md](DISASTER_RECOVERY.md) | Backup failure, restore drill, RTO/RPO breach |
| Пилотная поддержка | [PILOT_SUPPORT_RUNBOOK.md](PILOT_SUPPORT_RUNBOOK.md) | Intake, triage, bugfix release gate |
| HITL и управление | [GOVERNANCE.md](GOVERNANCE.md), [COUNCIL_GUIDE.md](COUNCIL_GUIDE.md) | Вето, quorum, 2FA, политики |
| RL-KPI | [GOVERNANCE.md](GOVERNANCE.md), `docs/modules/analytics-engine.md` | Предложение оптимизации, approval, effect measurement |
| База знаний | [KNOWLEDGE_BASE.md](KNOWLEDGE_BASE.md) | Повторяющийся вопрос, изменение процедуры, обучение |

## 7. readiness checklist

- [ ] Tenant проходит через marketplace moderation, а не ручной перенос данных.
- [ ] Назначены `tenant-admin`, `board`, `council`, `support`, `sre-oncall`.
- [ ] Resource plan применен и проверен через tenant-local counters.
- [ ] Все сервисные логи, метрики, события и audit records содержат `tenant_id`.
- [ ] Настроены support channels и P0/P1 escalation.
- [ ] SRE alerts доставляются в `sre-oncall`, `security-privacy` и
      `council-escalation`.
- [ ] Backup policy, restore drill и RTO/RPO подтверждены.
- [ ] HITL, 2FA, окно вето и quorum rules включены для чувствительных действий.
- [ ] Команда прошла обучение, evidence хранится без ПДн и секретов.
- [ ] База знаний содержит entrypoint, владельца, review cadence и дату
      последней проверки.

## 8. Evidence policy

Разрешено хранить в документации и evidence:

- `tenant_id`, `tenant_slug`, роли, synthetic handles и обезличенные счетчики;
- технические timestamps, статусы, checks, SHA256-хэши и correlation_id;
- ссылки на документы, тесты, CI workflow и runbook'и.

Запрещено хранить реальные ПДн, секреты, bearer credentials, платежные
реквизиты, суммы выплат, сырой голос, закрытое содержимое и площадочные
credentials. Любой случай обнаружения таких данных переводится в P0/P1 по
[SRE_RUNBOOK.md](SRE_RUNBOOK.md) и security/privacy escalation.

## 9. Локальная проверка

```bash
pytest tests/test_operations_training_issue102_acceptance_contract.py
```

Полный PR gate остается стандартным:

```bash
ruff check .
ruff format --check .
black --check .
mypy .
pytest
```
