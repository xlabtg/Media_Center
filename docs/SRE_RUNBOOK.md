# SRE runbook, SLA/SLO и алертинг

Дата фиксации: 2026-06-20.

Статус: sre-ready для issue #98.

Документ фиксирует эксплуатационный контур этапа 8: ownership сервисов,
целевые SLA/SLO, error budget, маршрутизацию алертов и runbooks для типовых
инцидентов. Структурный каталог целей находится в
`infra/observability/slo-targets.json`, правила Prometheus - в
`infra/observability/prometheus/rules/sre-alerts.yml`, маршруты Alertmanager -
в `infra/observability/alertmanager.yml`. Backup/DR-процедуры этапа 8
опубликованы в [DISASTER_RECOVERY.md](DISASTER_RECOVERY.md), а структурная
политика - в `infra/backup/backup-policy.json`. Единый эксплуатационный пакет
для tenant-команд опубликован в `docs/OPERATIONS_MANUAL.md`, а быстрые ответы
и workflow обновления материалов - в `docs/KNOWLEDGE_BASE.md`. Chaos-сценарии
отказов PostgreSQL, RabbitMQ, external API и proxy опубликованы в
[docs/CHAOS_TESTING.md](CHAOS_TESTING.md). Контракт проверяется тестом
`tests/test_sre_issue98_acceptance_contract.py`.

Все evidence, алерты, постмортемы и тестовые события ведутся по политике
`no_pdn_no_secrets`: без ПДн, токенов, bearer credentials, платежных реквизитов,
сырых материалов, сумм выплат и закрытого содержимого. Для диагностики
допустимы только `tenant_id`, `service`, `operation`, `status`,
`correlation_id`, технические timestamps, SHA256-хэши и обезличенное описание
влияния.

## 1. Критерии приемки #98

| Критерий | Как выполняется | Проверка |
|----------|-----------------|----------|
| Runbooks опубликованы | Этот документ задает порядок triage, escalation, mitigation, rollback и postmortem для P0-P3, включая `tenant_isolation`, `payout_halted`, `publication_backlog`, `private_blockchain_degraded` и `observability_pipeline_down`. | `tests/test_sre_issue98_acceptance_contract.py` |
| SLA/SLO определены и мониторятся | `infra/observability/slo-targets.json` задает business SLA, availability SLO, latency SLO, error budget и Prometheus selectors по сервисам. | `infra/observability/slo-targets.json`, Prometheus recording rules |
| Алертинг настроен и протестирован | Prometheus загружает `sre-alerts.yml`, Alertmanager маршрутизирует `critical`, `security` и обычные SRE alerts, локальный compose поднимает отдельный `alertmanager`. | `infra/local/docker-compose.yml`, `infra/observability/alertmanager.yml` |

## 2. Ownership и RACI

| Область | Responsible | Accountable | Consulted | Informed |
|---------|-------------|-------------|-----------|----------|
| API Gateway, tenant routing, rate limits | backend-oncall | sre-lead | security, council | support |
| Contribution Ledger и Weight Engine | ledger-oncall | backend-lead | council, analytics | support |
| HITL Payout Gateway | payout-oncall | council-duty | security, wallet | board |
| Messenger Adapter и публикации | messenger-oncall | operations-lead | compliance, support | council |
| Blockchain Auditor | chain-oncall | security-lead | council, backend | support |
| Observability pipeline | sre-oncall | sre-lead | backend, security | council |

`sre-oncall` принимает первичный алерт, подтверждает его в пределах SLA и
назначает доменного владельца. `council-duty` подключается к P0/P1, если есть
риск для HITL, выплат, политик Совета, межтенантной изоляции или публичного
запуска. `security-privacy` подключается к любому сигналу ПДн, token leak,
tenant leak, 2FA abuse или подозрению на обход RBAC.

## 3. SLA/SLO и error budget

Источник истины для числовых целей - `infra/observability/slo-targets.json`.

| Сервис | Business SLA | Availability SLO | Latency p95 | Error budget |
|--------|--------------|------------------|-------------|--------------|
| API Gateway | 99,9 % | 99,9 % | 250 мс | 0,1 % |
| Contribution Ledger | 99,5 % | 99,5 % | 500 мс | 0,5 % |
| HITL Payout Gateway | 99,5 % | 99,5 % | 700 мс | 0,5 % |
| Messenger Adapter | 99,0 % | 99,0 % | 1500 мс | 1,0 % |
| Blockchain Auditor | 99,5 % | 99,5 % | 1000 мс | 0,5 % |
| Observability | 99,5 % | 99,5 % | 500 мс | 0,5 % |

Error budget считается по tenant-aware операциям:

```promql
sum by (tenant_id, service) (
  rate(nmc_service_operations_total{status=~"error|denied"}[5m])
)
/
clamp_min(
  sum by (tenant_id, service) (rate(nmc_service_operations_total[5m])),
  1
)
```

Если burn rate держится выше 1 % в течение 15 минут, `sre-oncall` обязан
остановить небезопасные изменения для затронутого tenant/service, открыть
incident record и перевести работу в mitigation-first режим. Для P0/P1 новые
релизы разрешены только как hotfix с воспроизводящим тестом и rollback plan.

## 4. Приоритеты инцидентов

| Приоритет | Примеры | Ack SLA | Mitigation SLA | Эскалация |
|-----------|---------|---------|----------------|-----------|
| P0 | tenant leak, ПДн/секрет в логах, обход HITL/2FA, остановка выплат, потеря audit-chain quorum | 15 минут | 60 минут | Security + Совет + владелец сервиса |
| P1 | деградация SLO нескольких tenant, длительный publication backlog, недоступен council report, blockchain degraded без потери quorum | 30 минут | 4 часа | SRE + владелец сервиса + Совет по риску |
| P2 | локальная деградация одного tenant с workaround, рост latency p95, одиночные ошибки операций | 4 часа | 1 рабочий день | SRE + владелец сервиса |
| P3 | документация, шумный алерт, косметика dashboard, плановый debt | 1 рабочий день | По плану команды | Владелец сервиса |

P0/P1 всегда требуют postmortem в течение 2 рабочих дней. Postmortem содержит
timeline, impact, root cause, что сработало, что не сработало, corrective
actions, владельцев и дату проверки. Запрещено включать ПДн, токены, платежные
суммы, закрытые материалы или сырые payload.

## 5. Runbooks типовых инцидентов

### 5.1. `tenant_isolation`

Триггеры: `NmcTenantOperationErrors` с `operation=tenant_isolation`,
security audit event, cross-tenant denial spike, жалоба поддержки.

1. Подтвердить `tenant_id`, `service`, `operation`, `correlation_id` и время.
2. Остановить затронутый сценарий для tenant, не отключая другие tenant.
3. Проверить последние изменения Gateway, repository filters, cache keys,
   queue routing keys, vector filters и S3 prefixes.
4. Если есть риск доступа к чужим данным, классифицировать как P0,
   подключить `security-privacy` и `council-duty`.
5. Выпустить hotfix только с негативным тестом на `403
   tenant_isolation_violation`.
6. Обновить postmortem и риск-регистр, если нарушение подтвердилось.

### 5.2. `payout_halted`

Триггеры: нет прогресса очереди HITL, рост `payout.queued`, ошибки 2FA,
ошибка платежного коннектора, council veto loop.

1. Заморозить автоматическое продвижение выплат, но сохранить audit history.
2. Проверить очередь, окно вето, 2FA confirmation, payment connector и
   blockchain write receipt.
3. Сообщить Совету статус: affected tenant, количество операций, без сумм и
   платежных реквизитов.
4. Для P0/P1 включить manual approval mode и запретить retry без решения
   Совета.
5. После восстановления сверить события `payout.queued`,
   `payout.confirmed`, `payout.executed` и hash-only audit records.

### 5.3. `publication_backlog`

Триггеры: рост очереди публикаций, ошибки площадок, rate limit, fallback route
unhealthy, деградация Messenger Adapter.

1. Оценить backlog по tenant/platform без публикации закрытого содержимого.
2. Проверить platform registry, token store status, proxy lease и разрешенные
   fallback channels.
3. Отключить только проблемную platform route; остальные tenant/platform не
   затрагивать.
4. Не обходить ToS площадки и не использовать неразрешенные fallback channels.
5. После восстановления сверить receipts, idempotency keys и contribution
   logging.

### 5.4. `private_blockchain_degraded`

Триггеры: `NmcPrivateBlockchainNodeDown`,
`NmcPrivateBlockchainQuorumAtRisk`, рост latency Blockchain Auditor,
ошибки batch writer.

1. Проверить доступность Besu validators и quorum 3 из 4.
2. Если quorum под риском, классифицировать как P0 и остановить операции,
   требующие неизменяемого audit receipt.
3. Не писать ПДн, суммы выплат или raw content в chain/debug logs.
4. Восстановить ноды, затем проверить batch hash, local audit records и verify
   API.
5. Зафиксировать restore timeline и corrective action.

### 5.5. `observability_pipeline_down`

Триггеры: `NmcObservabilityPipelineDown`, недоступен Prometheus, Grafana,
OpenTelemetry Collector или Alertmanager.

1. Проверить health `prometheus`, `grafana`, `otel-collector`,
   `alertmanager` в docker-compose или production runtime.
2. Если метрики недоступны более 15 минут, классифицировать как P1; если при
   этом есть активный P0/P1 incident, повысить до P0.
3. Переключить incident tracking на ручной журнал с timestamps и
   correlation_id.
4. Восстановить pipeline, затем проверить rule evaluation, alert delivery и
   Grafana datasource.

### 5.6. `backup_restore_failed`

Триггеры: неуспешный backup job, отсутствует checksum manifest, превышен RPO,
restore drill превысил RTO/RPO, не прошли `tenant_restore_integrity` или
`cross_tenant_access_denied`.

1. Открыть [DISASTER_RECOVERY.md](DISASTER_RECOVERY.md) и
   `infra/backup/backup-policy.json`.
2. Проверить затронутый компонент: PostgreSQL, ChromaDB или S3/MinIO.
3. Если есть риск потери audit state или tenant leak, классифицировать как P0 и
   подключить `security-privacy` и `council-duty`.
4. Запустить restore drill в изолированном sandbox и сверить checksum,
   tenant_id, RTO и RPO.
5. Зафиксировать corrective action без ПДн, токенов, сумм выплат и raw content.

## 6. Alert routing

Prometheus оценивает rules из `infra/observability/prometheus/rules/*.yml` и
отправляет события в Alertmanager. Базовые маршруты:

- `severity="critical"` -> `council-escalation` и `sre-oncall`;
- `team="security"` -> `security-privacy`;
- все остальные SRE alerts -> `sre-oncall`.

Alertmanager в локальном контуре не содержит внешних секретов. Production
webhook/chat/email endpoints задаются через deployment secrets вне
репозитория.

## 7. Тестирование алертинга

Локальный контракт issue #98:

```bash
pytest tests/test_sre_issue98_acceptance_contract.py
```

Проверка конфигурации Prometheus/Alertmanager в локальном compose:

```bash
make up
docker compose --project-name media-center-local --env-file infra/local/.env.local.example -f infra/local/docker-compose.yml ps prometheus alertmanager
```

Перед production launch SRE проводит drill:

1. Сгенерировать synthetic `NmcSloAvailabilityBurnRateHigh` на dev tenant.
2. Подтвердить доставку в `sre-oncall`.
3. Проверить escalation для `severity="critical"` и `team="security"`.
4. Закрыть alert, убедиться в `send_resolved`.
5. Добавить запись drill без ПДн/секретов.

Полный PR gate остается стандартным:

```bash
ruff check .
ruff format --check .
black --check .
mypy .
pytest
```
