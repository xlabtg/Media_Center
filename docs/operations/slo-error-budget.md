# SLO и error budget ключевых сервисов

Дата фиксации: 2026-06-23.

Статус: F4 / #254, REQ-N5.

Документ фиксирует SLI/SLO и error budget для ключевых сервисов этапа 9:
`api-gateway`, `contribution-ledger`, `wallet`. Числовой каталог находится в
`infra/observability/slo-targets.json`, а Prometheus rules с burn rate alerting
находятся в `infra/observability/prometheus/rules/slo-error-budget.yml`.
Alertmanager получает эти alerts через общий маршрут
`infra/observability/alertmanager.yml`: `severity="critical"` эскалируется в
`council-escalation`, остальные SRE alerts остаются у `sre-oncall`.

## SLI

SLI строятся только из tenant-aware Prometheus metrics, которые публикуют
сервисы через `/metrics`:

- availability: доля операций без `status=~"error|denied"` в
  `nmc_service_operations_total`;
- latency: p95 из `nmc_service_operation_duration_seconds_bucket`;
- error budget burn: error ratio, нормализованный на бюджет сервиса.

Все ряды обязаны сохранять labels `tenant_id`, `service`, `operation`,
`status`. В metrics, alerts и incident notes не допускаются ПДн, токены,
сырые материалы, суммы выплат и закрытый контент.

## Цели

| Сервис | Availability SLO | Latency p95 | Error budget, 30 дней |
| --- | ---: | ---: | ---: |
| `api-gateway` | 99,9 % | 250 мс | 0,1 % |
| `contribution-ledger` | 99,5 % | 500 мс | 0,5 % |
| `wallet` | 99,5 % | 500 мс | 0,5 % |

`api-gateway` получает более жесткий availability SLO, потому что он является
общей точкой входа и tenant-aware маршрутизации. `contribution-ledger` и
`wallet` используют одинаковый 99,5 % SLO: оба сервиса критичны для учета, но
имеют больше доменных проверок и могут безопасно деградировать через
mitigation flow.

## Burn Rate

Prometheus считает error ratio по окнам `5m`, `30m`, `1h`, `6h`, затем делит
его на error budget fraction сервиса:

```promql
nmc:slo_error_ratio:ratio5m{service="api-gateway"} / 0.001
nmc:slo_error_ratio:ratio5m{service=~"contribution-ledger|wallet"} / 0.005
```

Fast burn alert срабатывает, когда оба окна `5m` и `1h` выше `14.4x`. Это
критический сигнал: сервис быстро расходует бюджет и требует mitigation-first
режима.

Slow burn alert срабатывает, когда оба окна `30m` и `6h` выше `6x`. Это
предупреждение: бюджет расходуется устойчиво, но есть время на triage до
критической эскалации.

## API Gateway

Owner: `backend-oncall`.

Prometheus alerts:

- `NmcApiGatewaySloErrorBudgetFastBurn`, critical;
- `NmcApiGatewaySloErrorBudgetSlowBurn`, warning;
- `NmcApiGatewaySloLatencyP95Breached`, warning, p95 > 250 мс;
- `NmcApiGatewaySloAvailabilityBreached`, critical, availability < 99,9 %.

Triage:

1. Проверить tenant routing, rate limits, auth failures и downstream service.
2. Остановить небезопасные изменения для затронутого tenant/service.
3. При critical burn открыть incident record и связать его с deployment SHA.

## Contribution Ledger

Owner: `ledger-oncall`.

Prometheus alerts:

- `NmcContributionLedgerSloErrorBudgetFastBurn`, critical;
- `NmcContributionLedgerSloErrorBudgetSlowBurn`, warning;
- `NmcContributionLedgerSloLatencyP95Breached`, warning, p95 > 500 мс;
- `NmcContributionLedgerSloAvailabilityBreached`, critical, availability < 99,5 %.

Triage:

1. Проверить операции вклада, weight engine, payout exporter и tenant filters.
2. Сверить рост `status="error"` и `status="denied"` по operation.
3. Если есть риск tenant isolation, перейти в runbook `tenant_isolation`.

## Wallet

Owner: `wallet-oncall`.

Prometheus alerts:

- `NmcWalletSloErrorBudgetFastBurn`, critical;
- `NmcWalletSloErrorBudgetSlowBurn`, warning;
- `NmcWalletSloLatencyP95Breached`, warning, p95 > 500 мс;
- `NmcWalletSloAvailabilityBreached`, critical, availability < 99,5 %.

Triage:

1. Проверить запись операций, idempotency conflicts, balance reads и tenant
   repository filters.
2. При связке с выплатами использовать runbook `payout_halted`.
3. Не публиковать суммы, member id и платежные реквизиты в incident notes.

## Проверка

Локальный контракт:

```bash
python -m pytest tests/test_slo_error_budget_issue254_contract.py
```

Полный локальный gate для observability:

```bash
python -m pytest \
  tests/test_observability_contract.py \
  tests/test_sre_issue98_acceptance_contract.py \
  tests/test_slo_error_budget_issue254_contract.py
```
