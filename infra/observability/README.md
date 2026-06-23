# Observability

`infra/observability` содержит локальный baseline для сквозной наблюдаемости
НМЦ: Prometheus, Grafana, Alertmanager и OpenTelemetry Collector. Конфигурации
предназначены для разработки и smoke-проверок, но фиксируют продуктовый
контракт:

- метрики экспортируются в формате Prometheus и содержат labels `tenant_id`,
  `service`, `operation`, `status`;
- структурные логи пишутся как JSON с `tenant_id`, `correlation_id`,
  `trace_id`/`span_id` при наличии;
- трассировка использует W3C `traceparent`, OTLP gRPC/HTTP и span attributes
  `tenant_id`, `service.name`, `operation`, `correlation_id`;
- ПДн, токены, bearer credentials, сырое содержимое и суммы выплат не попадают
  в метрики, логи, traces или dashboards.

## Локальный запуск

Observability-сервисы входят в общий compose:

```bash
make up
```

Адреса по умолчанию:

| Компонент | Адрес |
|-----------|-------|
| Prometheus | `http://localhost:9090` |
| Alertmanager | `http://localhost:9093` |
| Grafana | `http://localhost:3000` |
| OpenTelemetry gRPC | `localhost:4317` |
| OpenTelemetry HTTP | `http://localhost:4318` |

Grafana автоматически подключает Prometheus и дашборды
`НМЦ / Tenant Observability` и `НМЦ / DORA`. DORA dashboard issue #251
использует recording rules из `prometheus/rules/dora-metrics.yml` и источники
CI/CD + incident process, описанные в
`docs/case-studies/issue-213/metrics/dora-data-sources.md`. Dev-учётные данные
задаются только в `infra/local/.env.local.example` и предназначены для
локального запуска.

Для приватной blockchain-сети issue #79 используется compose override
`infra/observability/prometheus/prometheus.blockchain.yml`: он сохраняет
базовые scrape jobs и добавляет job `private-blockchain-besu` для Besu metrics
валидаторов. Alert rules лежат в
`infra/observability/prometheus/rules/blockchain-auditor.yml`.

## SLA/SLO и алертинг

SRE-контур issue #98 опубликован в [docs/SRE_RUNBOOK.md](../../docs/SRE_RUNBOOK.md).
Числовые business SLA, availability SLO, latency p95 и error budget по
сервисам зафиксированы в `slo-targets.json`. Prometheus загружает правила из
`prometheus/rules/sre-alerts.yml`, а Alertmanager использует
`alertmanager.yml` для маршрутизации:

- `severity="critical"` уходит в `council-escalation` и `sre-oncall`;
- `team="security"` уходит в `security-privacy`;
- остальные SRE alerts остаются у `sre-oncall`.

Локальная проверка контракта:

```bash
pytest tests/test_sre_issue98_acceptance_contract.py
```

## Метрики сервисов

Shared-библиотека публикует базовые метрики:

- `nmc_service_operations_total` — счётчик операций по tenant/service/status;
- `nmc_service_operation_duration_seconds` — histogram длительности операций.

Каждая метрика обязана иметь label `tenant_id`. Для системных self-check
endpoint можно использовать `tenant_id="system"`; доменные запросы должны
получать значение из проверенного tenant context.

## DORA-метрики

REQ-N3 закрывается dashboard `infra/observability/grafana/dashboards/dora.json`.
Он показывает Deployment frequency, Lead time for changes, Change failure rate и
MTTR через Prometheus recording rules:

- `nmc:dora_deployment_frequency:deploys_per_day`;
- `nmc:dora_lead_time:p75_seconds`;
- `nmc:dora_change_failure_rate:ratio30d`;
- `nmc:dora_mttr:avg_seconds`.

Исходные метрики `nmc_delivery_deployments_total`,
`nmc_delivery_lead_time_seconds_bucket`, `nmc_delivery_changes_total` и
`nmc_incident_recovery_seconds` поступают из GitHub Actions, GitHub Deployments и
incident process. Подробный контракт источников зафиксирован в
`docs/case-studies/issue-213/metrics/dora-data-sources.md`.

## Логи и traces

Логи формируются через `libs.shared.observability` и проходят privacy guard:
поля вроде `email`, `phone`, `access_token`, `authorization`, `amount` и
`raw_content` отклоняются до записи. Для межсервисной трассировки сервис
пробрасывает `traceparent`, `x-tenant-id` и `x-correlation-id`; OpenTelemetry
Collector принимает spans через OTLP и оставляет только технические атрибуты.
