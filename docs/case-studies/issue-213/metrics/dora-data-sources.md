# DORA data sources for issue #251

Статус: реализовано для issue #251 / REQ-N3. Этот контракт описывает, какие
события должны попадать в Prometheus, чтобы Grafana dashboard
`infra/observability/grafana/dashboards/dora.json` показывал четыре DORA-метрики:
Deployment frequency, Lead time for changes, Change failure rate и MTTR.

## Источники событий

| Источник | Событие | Метрика Prometheus | Обязательные labels |
| --- | --- | --- | --- |
| GitHub Actions | Успешная или неуспешная сборка release/deploy workflow | `nmc_delivery_changes_total` | `environment`, `service`, `workflow`, `outcome` |
| GitHub Deployments | Созданный deployment status для `production`/`staging` | `nmc_delivery_deployments_total` | `environment`, `service`, `status`, `sha` |
| GitHub Actions + GitHub Deployments | Интервал от commit timestamp до успешного deployment status | `nmc_delivery_lead_time_seconds_bucket` | `environment`, `service`, `le` |
| Incident process | Закрытый incident или rollback/postmortem record | `nmc_incident_recovery_seconds` (`_sum`, `_count`) | `environment`, `service`, `severity` |

Сырые события CI/CD и incident process не должны содержать ПДн, токены, суммы
выплат или raw content. В Prometheus попадают только технические labels,
агрегируемые счетчики и histogram buckets.

## Расчет DORA

Prometheus загружает `infra/observability/prometheus/rules/dora-metrics.yml` через
общий `rule_files` glob. Recording rules нормализуют источники в четыре ряда:

| DORA-метрика | Recording rule | Цель top-15% из `04-competitive-analysis.md` |
| --- | --- | --- |
| Deployment frequency | `nmc:dora_deployment_frequency:deploys_per_day` | `>= 1` деплой в день |
| Lead time for changes | `nmc:dora_lead_time:p75_seconds` | `< 1` день (`86400` секунд) |
| Change failure rate | `nmc:dora_change_failure_rate:ratio30d` | `< 5%` за 30 дней |
| MTTR | `nmc:dora_mttr:avg_seconds` | `< 1` час (`3600` секунд) |

Grafana использует эти recording rules напрямую и фильтрует значения по
переменным `environment` и `service`. Локальный compose провижинит dashboard
автоматически, потому что `infra/local/docker-compose.yml` монтирует
`../observability/grafana/provisioning` в `/etc/grafana/provisioning` и
`../observability/grafana/dashboards` в `/var/lib/grafana/dashboards`.

## Проверка

Контракт закреплен тестом:

```bash
python -m pytest tests/test_dora_grafana_issue251_contract.py
```

Тест проверяет provisioning Grafana, четыре панели dashboard, recording rules и
наличие этого документа как источника истины по CI/CD и incident data sources.
