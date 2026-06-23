# Матрица конкурентных метрик issue #253

Статус: живой документ для issue #253 / REQ-M4. Основа матрицы -
`docs/case-studies/issue-213/04-competitive-analysis.md`; текущие значения
обновляются при каждом release cut и сравниваются с целями Этапа 9.

Последнее обновление: 2026-06-23. Ответственный за обновление перед релизом:
release owner.

## Как читать матрицу

- Текущее значение - последний release snapshot или pull request artifact из
  измерительных контуров F1 / #251 и F2 / #252. Для метрик, где production
  событий ещё нет, зафиксирован текущий pre-prod baseline и правило замены на
  фактический ряд после первого production deployment.
- Целевое значение - контракт из `04-competitive-analysis.md`,
  `docs/operations/service-performance-budgets.json` и SLO-каталога.
- Источник текущего значения должен быть машинно проверяемым: CI artifact,
  Prometheus recording rule, Grafana dashboard, workflow evidence или
  структурный JSON-документ.

## Матрица

| Ось сравнения | Текущее значение | Целевое значение | Источник текущего значения | REQ / связь |
| --- | --- | --- | --- | --- |
| Размер образа | F2-гейт включен для всех 14 продуктовых сервисов; текущий release snapshot берется из artifact `service-performance-*`. Исторический baseline issue #232: `contribution-ledger` = 514 МБ до F2-оптимизации. | < 250 МБ на сервис; stretch < 200 МБ. | `docs/operations/service-performance-budgets.json`, `.github/scripts/check_service_performance_budget.py`, `.github/workflows/build-service.yml`, artifact `service-performance-<service>`. | REQ-N1, F2 / #252, REQ-M4 |
| Cold-start до `/ready` | F2-гейт измеряет `cold_start_ms` от запуска контейнера до первого HTTP 200 на `/ready` для каждого сервиса; текущий release snapshot берется из artifact `service-performance-*`. | <= 3 с на сервис. | `docs/operations/service-performance-budgets.json`, `.github/scripts/check_service_performance_budget.py`, `.github/workflows/build-service.yml`, `docs/operations/image-size-budget.md`. | REQ-N2, F2 / #252, REQ-M4 |
| Deployment frequency | Pre-prod baseline: 0 production deployments/day до первого production release; после релиза значение берется из rolling Prometheus row. | >= 1 деплой/день. | `nmc:dora_deployment_frequency:deploys_per_day`, `infra/observability/grafana/dashboards/dora.json`, `infra/observability/prometheus/rules/dora-metrics.yml`, `docs/case-studies/issue-213/metrics/dora-data-sources.md`. | REQ-N3, F1 / #251, REQ-M4 |
| Lead time for changes | Pre-prod baseline: 0 production deployment samples; после первого release фиксируется p75 commit-to-production из DORA recording rule. | < 1 день. | `nmc:dora_lead_time:p75_seconds`, `infra/observability/grafana/dashboards/dora.json`, `infra/observability/prometheus/rules/dora-metrics.yml`, `docs/case-studies/issue-213/metrics/dora-data-sources.md`. | REQ-N3, F1 / #251, REQ-M4 |
| Change failure rate | Pre-prod baseline: 0 failed production changes / 0 production changes; после первого release фиксируется rolling 30d ratio. | < 5 %. | `nmc:dora_change_failure_rate:ratio30d`, `infra/observability/grafana/dashboards/dora.json`, `infra/observability/prometheus/rules/dora-metrics.yml`, `docs/case-studies/issue-213/metrics/dora-data-sources.md`. | REQ-N3, F1 / #251, REQ-M4 |
| MTTR | Pre-prod baseline: 0 закрытых production incidents; после первого incident фиксируется среднее время восстановления из incident process. | < 1 час. | `nmc:dora_mttr:avg_seconds`, `infra/observability/grafana/dashboards/dora.json`, `infra/observability/prometheus/rules/dora-metrics.yml`, `docs/case-studies/issue-213/metrics/dora-data-sources.md`. | REQ-N3, F1 / #251, REQ-M4 |
| Supply-chain | Release workflow уже содержит Trivy gate по HIGH/CRITICAL, SBOM SPDX artifact, cosign keyless signature и SLSA provenance attestation для main/tag публикаций. | SBOM + cosign + SLSA, 0 HIGH/CRITICAL уязвимостей. | `.github/workflows/build-service.yml`, `docs/operations/image-signing-verification.md`, Trivy/SBOM/attestation artifacts release run. | REQ-N4, эпик C, REQ-M4 |
| SLO доступности | SLO-каталог stage 8 задает 99,9 % для `api-gateway`, 99,5 % для `contribution-ledger`, `hitl-payout-gateway`, `blockchain-auditor` и `observability`, 99,0 % для `messenger-adapter`; error budget от 0,1 % до 1,0 %. | Формализованный SLO + error budget; ключевой публичный edge target 99,9 %. | `infra/observability/slo-targets.json`, `docs/SRE_RUNBOOK.md`, Prometheus SLO alerts и incident process. | REQ-N5, F4 follow-up, REQ-M4 |

## Привязка к F1/F2

F1 / #251 владеет измерением DORA: Grafana dashboard
`infra/observability/grafana/dashboards/dora.json` показывает Deployment
frequency, Lead time for changes, Change failure rate и MTTR, а
`infra/observability/prometheus/rules/dora-metrics.yml` нормализует события в
recording rules.

F2 / #252 владеет измерением размера образа и cold-start:
`.github/scripts/check_service_performance_budget.py` читает размер из
`docker image ls`, измеряет `/ready`, пишет `service-performance-*` artifacts и
падает при нарушении `docs/operations/service-performance-budgets.json`.

Эта матрица не заменяет F1/F2, а является release-facing snapshot: по ней видно,
где НМЦ уже соответствует целям `04-competitive-analysis.md`, а где следующий
релиз должен улучшить текущий показатель.

## Процесс обновления по релизам

1. Перед release freeze release owner находит последний релевантный CI run:
   `gh run list --repo xlabtg/Media_Center --branch main --limit 5 --json databaseId,conclusion,createdAt,headSha`.
2. Для выбранного run открыть подробности и логи:
   `gh run view <run-id> --repo xlabtg/Media_Center --log`. Если run не
   passing, сначала сохранить и разобрать логи, затем обновлять матрицу только
   после исправления.
3. Скачать или открыть artifacts `service-performance-*` и обновить текущие
   значения строк "Размер образа" и "Cold-start до `/ready`": максимальное
   значение по сервисам, список нарушителей, запас до < 250 МБ и <= 3 с.
4. В Grafana или Prometheus снять F1 rows
   `nmc:dora_deployment_frequency:deploys_per_day`,
   `nmc:dora_lead_time:p75_seconds`,
   `nmc:dora_change_failure_rate:ratio30d` и
   `nmc:dora_mttr:avg_seconds`; заменить pre-prod baseline фактическими
   rolling values для release environment.
5. Проверить supply-chain evidence в release run: Trivy artifact без
   HIGH/CRITICAL, SBOM artifact, cosign signature и SLSA provenance attestation.
6. Сверить SLO строку с `infra/observability/slo-targets.json`, Grafana SLO
   panels и incident process. Если F4 меняет цели или ключевые сервисы,
   обновить эту матрицу в том же PR.
7. В release PR указать diff матрицы и ссылку на CI run/artifacts. Если
   изменились цели, источники измерений или contract-тест, обновить
   `docs/STAGE_9_ACCEPTANCE.md`.

## Проверка

Контракт живой матрицы закреплен тестом:

```bash
python -m pytest tests/test_competitive_metrics_matrix_issue253_contract.py
```

Тест проверяет наличие всех осей из competitive analysis, текущих и целевых
значений, привязку к F1/F2 и release-процесс обновления без placeholder-строк.
