# Infra

**Статус:** каркас инфраструктуры с базовой CI-сборкой сервисных образов,
локальной docker-compose средой и observability baseline.

## Назначение

`infra` хранит инфраструктурные артефакты, которые не относятся к коду
конкретного сервиса: локальную среду, deploy-конфигурации, observability и
операционные настройки.

## Подкаталоги

| Каталог | Назначение |
|---------|------------|
| `local/` | docker-compose для локальной разработки и smoke-проверок. |
| `blockchain/` | Optional compose-профиль Hyperledger Besu/QBFT для приватной audit-chain issue #79. |
| `deploy/` | Будущие deployment-манифесты и окружения. |
| `observability/` | Конфигурации Prometheus, Alertmanager, Grafana и OpenTelemetry Collector для метрик, алертов, логов и трейсинга. |
| `backup/` | Backup/DR policy issue #99: расписания, scripts, cron template и restore drill для PostgreSQL, ChromaDB и S3/MinIO. |
| `docker/` | Общие Dockerfile для CI-сборки сервисных образов. |

## Docker-образы сервисов

`docker/service.Dockerfile` собирает базовый образ для каждого сервисного
каталога из matrix в [CI](../.github/workflows/ci.yml). Пока продуктовый код не
добавлен, образ фиксирует runtime baseline `python:3.13.14-slim`, копирует
README сервиса и `libs/shared/README.md`, чтобы проверять build pipeline без
смешивания с реализацией будущих микросервисов.

Локальная smoke-сборка одного сервиса:

```bash
docker build \
  -f infra/docker/service.Dockerfile \
  --build-arg SERVICE_NAME=api-gateway \
  --build-arg SERVICE_PATH=services/api-gateway \
  -t media-center-api-gateway:local \
  .
```

## Локальная среда

`infra/local/docker-compose.yml` поднимает PostgreSQL, Redis, RabbitMQ,
ChromaDB, MinIO, Prometheus, Alertmanager, Grafana и OpenTelemetry Collector с
фиксированными версиями. Основной workflow:

```bash
make up
make migrate
make test
make down
```

Подробности, порты, env-шаблон, миграции, сиды и фикстуры описаны в
[local/README.md](local/README.md).

Минимальный production capacity-профиль `nmc-minimal-100upm`, включая
recommended-core сервисы, CPU/RAM requests/limits и инфраструктурный floor
16 vCPU / 32 GiB RAM, опубликован в
[docs/MINIMAL_PRODUCTION_RESOURCES.md](../docs/MINIMAL_PRODUCTION_RESOURCES.md).

## Приватная blockchain-сеть

[blockchain/](blockchain/) содержит локальный deploy-контур issue #79:
Hyperledger Besu 26.6.1, QBFT с 4 валидаторами, node permissioning,
внутренний alias `besu-auditor.internal` для `blockchain-auditor` и
Prometheus job `private-blockchain-besu`. Контур запускается явно:

```bash
make blockchain-config
make blockchain-up
```

RPC и P2P порты не публикуются на host; доступ к audit operations остается за
сервисом `blockchain-auditor`, где включены tenant isolation и council-only
RBAC.

## Наблюдаемость

`observability/` фиксирует локальный контракт issue #24:

- `prometheus/prometheus.yml` собирает `nmc_service_operations_total` и
  `nmc_service_operation_duration_seconds` с labels `tenant_id`, `service`,
  `operation`, `status`;
- `prometheus/prometheus.blockchain.yml` добавляет scrape job
  `private-blockchain-besu` для Besu-нод при запуске blockchain-профиля;
- `slo-targets.json`, `prometheus/rules/sre-alerts.yml` и `alertmanager.yml`
  фиксируют SRE-контур issue #98: SLA/SLO, error budget, alert routing и
  incident runbooks из [docs/SRE_RUNBOOK.md](../docs/SRE_RUNBOOK.md);
- `grafana/` содержит provisioning datasource и дашборд tenant overview;
- `otel-collector.yml` принимает OpenTelemetry traces/logs/metrics через OTLP и
  сохраняет только технические attributes без ПДн.

## Backup и аварийное восстановление

`backup/` фиксирует DR-контур issue #99. Источник истины -
`infra/backup/backup-policy.json`: расписания UTC, retention, RTO/RPO,
restore validation и evidence `drill-issue-99-2026-06-20`. Runbook находится в
[docs/DISASTER_RECOVERY.md](../docs/DISASTER_RECOVERY.md).

Локальная проверка без изменения volumes:

```bash
make backup-policy
make backup-local
make restore-drill
```

## Правила

- docker-compose и образы используют явные версии из
  [ADR-0006](../docs/adr/0006-technology-stack-and-versions.md), без `latest`.
- Секреты не коммитятся; допустимы только примеры и ссылки на `.env.example`.
- Observability-конфигурация не должна раскрывать ПДн, токены, сырое содержимое
  и суммы выплат.
