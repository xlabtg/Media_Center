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
каталога из matrix в [CI](../.github/workflows/ci.yml). Образ фиксирует runtime
baseline `python:3.13.14-slim`, устанавливает runtime-зависимости из
`[project].dependencies` в [pyproject.toml](../pyproject.toml) и использует
единую структуру артефакта:

```text
/app/
├── service/                 # код выбранного SERVICE_PATH
├── libs/                    # общие библиотеки монорепозитория
├── config/                  # конфиги и build metadata
│   └── build_info.json
└── logs/                    # writable-каталог для runtime-логов при необходимости
```

`WORKDIR` всегда `/app`, а `PYTHONPATH=/app/service:/app`, поэтому код сервиса
импортируется из `/app/service`, а общие модули из `/app/libs` доступны как
`libs.*`.

Локальная smoke-сборка одного сервиса:

```bash
docker build \
  -f infra/docker/service.Dockerfile \
  --build-arg SERVICE_NAME=api-gateway \
  --build-arg SERVICE_PATH=services/api-gateway \
  --build-arg SERVICE_VERSION="$(git describe --tags --always --dirty)" \
  --build-arg BUILD_DATE="$(date -u +'%Y-%m-%dT%H:%M:%SZ')" \
  --build-arg GIT_COMMIT="$(git rev-parse HEAD)" \
  --build-arg GIT_TAG="$(git describe --tags --exact-match 2>/dev/null || true)" \
  --build-arg IMAGE_SOURCE=https://github.com/xlabtg/Media_Center \
  -t media-center-api-gateway:local \
  .
```

При сборке образ пишет `/app/config/build_info.json` с `service`,
`version`, `build_date`, `git_commit`, `git_tag`, `python`,
`python_version` и `python_compiler`; те же build-аргументы используются для
OCI-меток `org.opencontainers.image.source`, `version`, `revision` и
`created`.

Runtime hardening для app-сервисов зафиксирован в
[docs/operations/container-hardening.md](../docs/operations/container-hardening.md):
non-root UID/GID `1000:1000`, `tini` как PID 1, writable только `/tmp` и
`/app/logs`, а также compose/k8s флаги `read_only`,
`no-new-privileges` и `cap_drop: ALL`.

Образ включает готовый `docker/entrypoint.sh`, который копируется в
`/app/entrypoint.sh` и запускается через `tini`. Поэтому `docker run` без
аргументов выполняет команду `serve`: entrypoint стартует `uvicorn` на
`APP_HOST=0.0.0.0` и `APP_PORT=7700`. ASGI import string можно задать явно через
`APP_MODULE`; если переменная не задана, entrypoint строит значение из
`SERVICE_NAME`, заменяя дефисы на подчёркивания: например
`SERVICE_NAME=contribution-ledger` даёт
`contribution_ledger_app.main:app`.

Smoke-запуск собранного сервиса:

```bash
docker run --rm \
  -e SERVICE_NAME=contribution-ledger \
  -e JWT_SECRET=local-jwt-secret \
  -p 7700:7700 \
  media-center-contribution-ledger:local
```

Для сервисов с нестандартным модулем задайте `APP_MODULE`, например
`APP_MODULE=app.main:app`. Любые аргументы вместо `serve` считаются override:
`docker run --rm media-center-contribution-ledger:local python -V` выполнит
переданную команду внутри контейнера.

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
