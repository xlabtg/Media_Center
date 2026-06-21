# Программа обучения команд tenant'ов

Дата фиксации: 2026-06-20.

Статус: training-complete для issue #102.

Документ фиксирует программу обучения stage-8 команд: администраторы tenant,
Совет, Правление, поддержка и SRE/on-call. Для issue #102 обучение считается
проведенным по синтетическому evidence без ПДн:
`docs/operations/tenant-training-record.json`.

Связанные материалы:
[OPERATIONS_MANUAL.md](OPERATIONS_MANUAL.md),
[KNOWLEDGE_BASE.md](KNOWLEDGE_BASE.md),
[SRE_RUNBOOK.md](SRE_RUNBOOK.md),
[DISASTER_RECOVERY.md](DISASTER_RECOVERY.md),
[TENANT_MARKETPLACE.md](TENANT_MARKETPLACE.md),
[GOVERNANCE.md](GOVERNANCE.md),
[COUNCIL_GUIDE.md](COUNCIL_GUIDE.md).

## 1. Цели обучения

После обучения tenant-команда должна самостоятельно:

- пройти tenant lifecycle от заявки до ежедневной эксплуатации;
- проверять readiness checklist перед запуском tenant;
- вести поддержку без ПДн, секретов и закрытого содержимого в evidence;
- классифицировать P0-P3 и передавать P0/P1 в SRE/security/Совет;
- понимать HITL, 2FA, окно вето, quorum rules и границы ролей;
- запускать backup/DR drill и читать RTO/RPO evidence;
- пользоваться базой знаний и обновлять материалы при изменении процедуры;
- проводить knowledge check и контрольный сценарий перед go/no-go.

## 2. Tracks

| Track | Для кого | Ключевые навыки | Итог |
|-------|----------|-----------------|------|
| `tenant-admin` | администраторы tenant, Правление | onboarding, роли, resource plan, support intake, база знаний | Готовность вести ежедневную эксплуатацию tenant |
| `council-governance` | Совет, Президиум, Правление | HITL, veto, 2FA, quorum, RL-KPI approval, compliance gate | Готовность принимать чувствительные решения |
| `support-triage` | поддержка, QA, Правление | P0-P3, incident evidence, bugfix release gate, FAQ update | Готовность принимать обращения без ПДн |
| `sre-dr` | SRE/on-call, backend owners | SLO/SLA, Alertmanager, backup/DR, restore drill, postmortem | Готовность реагировать на инциденты и восстановление |

## 3. Учебные модули

| Модуль | Материалы | Практика |
|--------|-----------|----------|
| Operations overview | [OPERATIONS_MANUAL.md](OPERATIONS_MANUAL.md) | Собрать readiness checklist для нового tenant |
| Tenant provisioning | [TENANT_MARKETPLACE.md](TENANT_MARKETPLACE.md), [MULTITENANT_SCALING.md](MULTITENANT_SCALING.md) | Проверить заявку, moderation и resource plan |
| Governance and HITL | [GOVERNANCE.md](GOVERNANCE.md), [COUNCIL_GUIDE.md](COUNCIL_GUIDE.md) | Разобрать veto/approve/reject решение без ПДн |
| Support and incidents | [PILOT_SUPPORT_RUNBOOK.md](PILOT_SUPPORT_RUNBOOK.md), [SRE_RUNBOOK.md](SRE_RUNBOOK.md) | Присвоить P0-P3, владельца, workaround и escalation |
| Backup/DR | [DISASTER_RECOVERY.md](DISASTER_RECOVERY.md) | Прочитать restore-drill evidence и проверить RTO/RPO |
| Continuous improvement | `docs/modules/analytics-engine.md`, `docs/modules/policy-manager.md` | Провести RL-KPI approval и effect measurement review |
| Knowledge base | [KNOWLEDGE_BASE.md](KNOWLEDGE_BASE.md) | Обновить быстрый ответ и назначить владельца |

## 4. Контрольный сценарий

Контрольный сценарий выполняется после лекционной части:

1. Tenant подает заявку в каталог и проходит moderation.
2. Tenant-admin применяет resource plan и проверяет `tenant_id` в evidence.
3. Support получает обращение о недоступности публикации и назначает P1.
4. SRE/on-call проверяет alert, mitigation и rollback.
5. Совет принимает решение по спорной публикации через HITL.
6. Backup owner читает restore drill и подтверждает RTO/RPO.
7. Knowledge-owner добавляет новый вопрос в базу знаний.

Зачетный критерий: команда не раскрывает ПДн/секреты, корректно назначает роли,
не обходит Совет для чувствительных решений и указывает документ-источник для
каждого шага.

## 5. knowledge check

Минимальный knowledge check:

- 10 вопросов по tenant lifecycle, RACI, P0/P1 и evidence policy;
- 5 вопросов для Совета по HITL, veto, quorum, 2FA и RL-KPI;
- 5 вопросов для SRE/on-call по Alertmanager, backup/DR, RTO/RPO и postmortem;
- практический разбор одного support case без ПДн;
- обновление одной записи базы знаний с владельцем и датой review.

Проходной порог - 90 %. Если track не проходит порог, команда повторяет
контрольный сценарий и обновляет пробелы в [KNOWLEDGE_BASE.md](KNOWLEDGE_BASE.md).

## 6. Evidence обучения

Файл `docs/operations/tenant-training-record.json` фиксирует:

- статус `training-complete`;
- дату завершения `2026-06-20`;
- обязательные роли;
- sessions по tracks `tenant-admin`, `council-governance`, `support-triage` и
  `sre-dr`;
- pass rate knowledge check;
- политику `no_pdn_no_secrets`;
- entrypoint базы знаний и review cadence 14 дней.

Evidence не содержит email, телефоны, реальные имена, секреты, площадочные
credentials, платежные реквизиты, суммы выплат и закрытые материалы.

## 7. Обновление программы

Программа обновляется при любом из событий:

- новый tenant lifecycle step или resource plan;
- изменение SRE/DR runbook'а, alert routing или RTO/RPO;
- P0/P1 postmortem с corrective action для обучения;
- изменение HITL, 2FA, quorum, RL-KPI или compliance gate;
- повторяющийся вопрос в базе знаний два раза или чаще за 14 дней.

Изменение программы требует обновить [KNOWLEDGE_BASE.md](KNOWLEDGE_BASE.md) и
при необходимости повторить knowledge check для затронутого track.

## 8. Проверка

```bash
pytest tests/test_operations_training_issue102_acceptance_contract.py
```
