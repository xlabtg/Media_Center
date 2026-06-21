# Acceptance snapshot этапа 8

Дата фиксации: 2026-06-21.

Статус: acceptance snapshot для issue #103.

Snapshot агрегирует приемку issue #97, issue #98, issue #99, issue #100,
issue #101 и issue #102.

Документ закрывает эпик [#103](https://github.com/xlabtg/Media_Center/issues/103)
как итоговую фиксацию перехода от пилота к многотенантной эксплуатации. Он не
является разрешением на промышленную обработку реальных ПДн, платежных
реквизитов или площадочных credentials: фактический production launch остается
ручным go/no-go решением Совета после legal/security review, проверки tenant
contracts, актуального CI и operational readiness drill.

## 1. Решение по этапу 8

Этап 8 считается готовым как проверяемый эксплуатационный пакет:

- несколько tenant'ов работают стабильно через tenant-local counters,
  `TenantResourcePlan`, SLO labels и marketplace provisioning;
- изоляция сохраняется под нагрузкой: параллельный контракт issue #97
  проверяет независимые request, concurrency, storage и queue limits;
- ресурсы управляются по tenant через `InMemoryTenantResourceManager` и
  `resource_plan`, применяемый после модерации заявки;
- SLA/SLO определены и мониторятся через `infra/observability/slo-targets.json`,
  Prometheus rules, Alertmanager routes и SRE runbook;
- алертинг настроен и протестирован для SRE, security/privacy и Council
  escalation маршрутов;
- бэкапы выполняются по расписанию для PostgreSQL, ChromaDB и S3/MinIO;
- restore drill прошел в пределах RTO/RPO и зафиксирован как
  `drill-issue-99-2026-06-20`;
- каталог отображает tenant'ов только после публикации moderated profile;
- подключение проходит модерацию ролями `council`, `presidium` или `board`;
- RL-KPI loop работает под контролем Совета: proposal, approval, effect
  measurement и rollback gate остаются supervised;
- эксплуатационная документация и обучение опубликованы в Operations Manual,
  Tenant Training Program и Knowledge Base;
- все evidence ведется по политике `no_pdn_no_secrets`.

Решение: можно переходить к подготовке промышленной эксплуатации нескольких
tenant'ов при соблюдении gate из раздела 8. Репозиторий фиксирует readiness
контракт, но не содержит реальные ПДн, секреты, платежные суммы, raw content
или площадочные credentials.

## 2. Критерии приемки issue #97

| Критерий | Статус | Проверяемые ссылки |
|----------|--------|--------------------|
| Платформа держит несколько tenant'ов | Выполнено: `InMemoryTenantResourceManager` хранит независимое состояние по `tenant_id`, а threaded load contract проверяет два tenant'а под параллельными операциями. | [docs/MULTITENANT_SCALING.md](MULTITENANT_SCALING.md), [tests/test_multitenant_scaling_issue97_contract.py](../tests/test_multitenant_scaling_issue97_contract.py) |
| Изоляция сохраняется под нагрузкой | Выполнено: `TenantScopedRepository` и `assert_only_tenant_records` подтверждают, что выборки tenant A и tenant B не смешиваются. | [libs/shared/tenant_resources.py](../libs/shared/tenant_resources.py), [docs/modules/tenant-isolation.md](modules/tenant-isolation.md) |
| Ресурсы управляются по tenant | Выполнено: `TenantResourcePlan` задает `request_limit`, `concurrent_operations`, `storage_bytes` и `queue_depth`, а отказ одного tenant не влияет на другого. | [services/api-gateway/README.md](../services/api-gateway/README.md), [tests/test_api_gateway_routing.py](../tests/test_api_gateway_routing.py) |

## 3. Критерии приемки issue #98

| Критерий | Статус | Проверяемые ссылки |
|----------|--------|--------------------|
| Runbooks опубликованы | Выполнено: SRE runbook описывает P0-P3, RACI, escalation, mitigation, rollback и postmortem для tenant isolation, payouts, publication backlog, blockchain и observability incidents. | [docs/SRE_RUNBOOK.md](SRE_RUNBOOK.md), [tests/test_sre_issue98_acceptance_contract.py](../tests/test_sre_issue98_acceptance_contract.py) |
| SLA/SLO определены и мониторятся | Выполнено: каталог целей задает business SLA, availability SLO, latency p95, error budget и Prometheus selectors по сервисам с обязательным `tenant_id`. | [infra/observability/slo-targets.json](../infra/observability/slo-targets.json), [infra/observability/prometheus/prometheus.yml](../infra/observability/prometheus/prometheus.yml) |
| Алертинг настроен и протестирован | Выполнено: Prometheus подключает SRE rules, Alertmanager маршрутизирует `sre-oncall`, `security-privacy` и `council-escalation`, локальный compose поднимает alertmanager. | [infra/observability/prometheus/rules/sre-alerts.yml](../infra/observability/prometheus/rules/sre-alerts.yml), [infra/observability/alertmanager.yml](../infra/observability/alertmanager.yml) |

## 4. Критерии приемки issue #99

| Критерий | Статус | Проверяемые ссылки |
|----------|--------|--------------------|
| Бэкапы выполняются по расписанию | Выполнено: backup policy и cron-шаблон задают UTC-расписания, retention и storage policy для PostgreSQL, ChromaDB и S3/MinIO. | [docs/DISASTER_RECOVERY.md](DISASTER_RECOVERY.md), [infra/backup/backup-policy.json](../infra/backup/backup-policy.json), [infra/backup/cron.d/nmc-backups.cron](../infra/backup/cron.d/nmc-backups.cron) |
| Восстановление протестировано, RTO/RPO соблюдены | Выполнено: restore drill `drill-issue-99-2026-06-20` завершился `passed`, observed RTO/RPO укладываются в целевые значения. | [infra/backup/scripts/restore_drill.sh](../infra/backup/scripts/restore_drill.sh), [tests/test_backup_dr_issue99_acceptance_contract.py](../tests/test_backup_dr_issue99_acceptance_contract.py) |
| Процедуры задокументированы | Выполнено: DR runbook описывает backup pipeline, restore sandbox, tenant validation, escalation и postmortem без ПДн/секретов. | [infra/backup/README.md](../infra/backup/README.md), [docs/SRE_RUNBOOK.md](SRE_RUNBOOK.md) |

## 5. Критерии приемки issue #100

| Критерий | Статус | Проверяемые ссылки |
|----------|--------|--------------------|
| Новый tenant подключается по сценарию | Выполнено: `TenantMarketplaceSubmission` проходит submitted -> moderation -> provisioned и после approve применяет `resource_plan`. | [docs/TENANT_MARKETPLACE.md](TENANT_MARKETPLACE.md), [libs/shared/tenant_marketplace.py](../libs/shared/tenant_marketplace.py) |
| Каталог отображает tenant'ов | Выполнено: `list_catalog()` возвращает только `published` profiles и не раскрывает `contact_ref`, ПДн или секреты. | [tests/test_tenant_marketplace_issue100_acceptance_contract.py](../tests/test_tenant_marketplace_issue100_acceptance_contract.py) |
| Подключение проходит модерацию | Выполнено: решения `approve`, `request_changes` и `reject` доступны только ролям `council`, `presidium` и `board`. | [docs/ARCHITECTURE.md](ARCHITECTURE.md), [docs/DATA_MODEL.md](DATA_MODEL.md) |

## 6. Критерии приемки issue #101

| Критерий | Статус | Проверяемые ссылки |
|----------|--------|--------------------|
| Контур RL-KPI работает в проде | Выполнено как supervised production contract: Analytics Engine создает RL-KPI iterations на окне 7-30 дней, публикует события и хранит tenant-aware audit. | [docs/modules/analytics-engine.md](modules/analytics-engine.md), [services/analytics-engine/README.md](../services/analytics-engine/README.md), [tests/test_rl_kpi_loop_issue101_acceptance_contract.py](../tests/test_rl_kpi_loop_issue101_acceptance_contract.py) |
| Изменения утверждаются Советом | Выполнено: approval endpoint требует роль `council`, Policy Manager проверяет `rl_kpi.require_council_approval`, Governance фиксирует ручной контроль. | [docs/modules/policy-manager.md](modules/policy-manager.md), [services/policy-manager/README.md](../services/policy-manager/README.md), [docs/GOVERNANCE.md](GOVERNANCE.md) |
| Эффект изменений измеряется | Выполнено: effect endpoint сравнивает baseline/evaluation periods, считает absolute/relative delta и рекомендует rollback при деградации. | [services/analytics-engine/analytics_engine/api.py](../services/analytics-engine/analytics_engine/api.py) |

## 7. Критерии приемки issue #102

| Критерий | Статус | Проверяемые ссылки |
|----------|--------|--------------------|
| Документация эксплуатации полна и актуальна | Выполнено: Operations Manual связывает tenant lifecycle, RACI, readiness checklist, SRE, Backup/DR, Marketplace, RL-KPI и Knowledge Base. | [docs/OPERATIONS_MANUAL.md](OPERATIONS_MANUAL.md), [tests/test_operations_training_issue102_acceptance_contract.py](../tests/test_operations_training_issue102_acceptance_contract.py) |
| Проведено обучение команд tenant'ов | Выполнено: training program опубликован, а структурированное evidence `training-complete` хранится без ПДн и секретов. | [docs/TENANT_TRAINING_PROGRAM.md](TENANT_TRAINING_PROGRAM.md), [docs/operations/tenant-training-record.json](operations/tenant-training-record.json) |
| База знаний доступна | Выполнено: Knowledge Base содержит карту runbook'ов, быстрые ответы, владельцев и 14-дневный review cadence. | [docs/KNOWLEDGE_BASE.md](KNOWLEDGE_BASE.md) |

## 8. Gate промышленной эксплуатации

Перед фактической эксплуатацией нескольких tenant'ов Совет и SRE проводят
ручной go/no-go:

- каждый production tenant проходит onboarding через marketplace moderation,
  а не ручной перенос данных;
- реальные контакты, согласия и ПДн загружаются только в tenant-scoped контур
  вне репозитория и после legal/privacy review;
- SLO catalog, Prometheus rules, Grafana dashboards, Alertmanager routes и
  on-call расписание актуальны для всех включаемых tenant'ов;
- backup jobs и restore drill проверены на sandbox, RTO/RPO не превышены;
- tenant resource plan применен, request/concurrency/storage/queue counters
  проверены под нагрузкой;
- HITL, 2FA, окно вето, quorum rules и council escalation включены для
  чувствительных действий;
- RL-KPI changes проходят только через proposal, Council approval, effect
  measurement и rollback decision;
- support queue, SRE runbook, DR runbook, Operations Manual, Training Program
  и Knowledge Base выданы tenant-командам;
- P0/P1 incidents отсутствуют или имеют owner, mitigation, rollback и
  postmortem plan;
- CI по текущему commit зеленый, а локальный acceptance из раздела 9 пройден.

## 9. Локальная проверка

Минимальный stage-8 acceptance:

```bash
pytest tests/test_stage8_acceptance_contract.py
pytest tests/test_multitenant_scaling_issue97_contract.py
pytest tests/test_sre_issue98_acceptance_contract.py
pytest tests/test_backup_dr_issue99_acceptance_contract.py
pytest tests/test_tenant_marketplace_issue100_acceptance_contract.py
pytest tests/test_rl_kpi_loop_issue101_acceptance_contract.py
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
