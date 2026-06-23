# Локальная среда разработки

`infra/local` содержит воспроизводимый docker-compose для разработки и
smoke-проверок платформы НМЦ. Стек поднимает PostgreSQL, Redis, RabbitMQ,
ChromaDB, MinIO, Prometheus, Grafana, OpenTelemetry Collector и
приложенческие сервисы на едином внутреннем порту `7700` с версиями из
[ADR-0006](../../docs/adr/0006-technology-stack-and-versions.md), локального
observability baseline и Stage 9 container runtime hardening.

## Быстрый старт

Требования:

- Docker с compose plugin;
- свободные локальные порты `5432`, `6379`, `5672`, `15672`, `8001`, `9000`,
  `9001`, `9090`, `3000`, `4317`, `4318`, `8889`;
- свободные локальные app-порты `7701`-`7714`, если не переопределять их в
  `infra/local/.env.local`.

Запуск:

```bash
make up
make migrate
make test
```

Остановка:

```bash
make down
```

Удаление volumes и полная пересборка состояния:

```bash
make clean
make up
make migrate
```

По умолчанию команды используют безопасный шаблон
`infra/local/.env.local.example`. Для локальных переопределений можно создать
свой файл и передать его явно:

```bash
LOCAL_ENV_FILE=infra/local/.env.local make up
LOCAL_ENV_FILE=infra/local/.env.local make migrate
```

Файл `.env.local` игнорируется git. Коммитить можно только шаблоны без реальных
секретов.

## Приватная blockchain-сеть

Для issue #79 есть отдельный optional profile в
`infra/blockchain/docker-compose.yml`. Он поднимает 4 validator-ноды
Hyperledger Besu/QBFT, не публикует RPC/P2P порты на host и добавляет
Prometheus job `private-blockchain-besu`.

```bash
make blockchain-config
make blockchain-up
make blockchain-down
```

`BLOCKCHAIN_AUDITOR_URL` в dev-шаблоне указывает на внутренний
`grpc://besu-auditor.internal:50051`; низкоуровневый Besu RPC остается внутри
compose-сети как `http://besu-rpc:8545`.

## Сервисы и порты

| Сервис | Образ | Локальный адрес |
|--------|-------|-----------------|
| PostgreSQL | `postgres:17` | `localhost:5432` |
| Redis | `redis:7.4` | `localhost:6379` |
| RabbitMQ | `rabbitmq:4.1-management` | AMQP `localhost:5672`, UI `http://localhost:15672` |
| ChromaDB | `chromadb/chroma:1.5.9` | `http://localhost:8001` |
| MinIO | `minio/minio:RELEASE.2025-09-07T16-13-09Z` | API `http://localhost:9000`, UI `http://localhost:9001` |
| Prometheus | `prom/prometheus:v3.5.4` | `http://localhost:9090` |
| Grafana | `grafana/grafana:12.4.4` | `http://localhost:3000` |
| OpenTelemetry Collector | `otel/opentelemetry-collector-contrib:0.154.0` | OTLP gRPC `localhost:4317`, OTLP HTTP `http://localhost:4318`, Prometheus exporter `http://localhost:8889` |
| Activity Command Center | `ghcr.io/xlabtg/media-center-activity-command-center:${IMAGE_TAG}` | `http://localhost:7701`, container `7700` |
| Analytics Engine | `ghcr.io/xlabtg/media-center-analytics-engine:${IMAGE_TAG}` | `http://localhost:7702`, container `7700` |
| API Gateway | `ghcr.io/xlabtg/media-center-api-gateway:${IMAGE_TAG}` | `http://localhost:7703`, container `7700` |
| Blockchain Auditor | `ghcr.io/xlabtg/media-center-blockchain-auditor:${IMAGE_TAG}` | `http://localhost:7704`, container `7700` |
| CGLR | `ghcr.io/xlabtg/media-center-cglr:${IMAGE_TAG}` | `http://localhost:7705`, container `7700` |
| Contribution Ledger | `ghcr.io/xlabtg/media-center-contribution-ledger:${IMAGE_TAG}` | `http://localhost:7706`, container `7700` |
| HITL Payout Gateway | `ghcr.io/xlabtg/media-center-hitl-payout-gateway:${IMAGE_TAG}` | `http://localhost:7707`, container `7700` |
| Messenger Adapter | `ghcr.io/xlabtg/media-center-messenger-adapter:${IMAGE_TAG}` | `http://localhost:7708`, container `7700` |
| Neuro Agent Orchestrator | `ghcr.io/xlabtg/media-center-neuro-agent-orchestrator:${IMAGE_TAG}` | `http://localhost:7709`, container `7700` |
| Notification Gateway | `ghcr.io/xlabtg/media-center-notification-gateway:${IMAGE_TAG}` | `http://localhost:7710`, container `7700` |
| Policy Manager | `ghcr.io/xlabtg/media-center-policy-manager:${IMAGE_TAG}` | `http://localhost:7711`, container `7700` |
| Voice to Chain | `ghcr.io/xlabtg/media-center-voice-to-chain:${IMAGE_TAG}` | `http://localhost:7712`, container `7700` |
| Wallet | `ghcr.io/xlabtg/media-center-wallet:${IMAGE_TAG}` | `http://localhost:7713`, container `7700` |
| Web Cabinet | `ghcr.io/xlabtg/media-center-web-cabinet:${IMAGE_TAG}` | `http://localhost:7714`, container `7700` |

Dev-логины из `infra/local/.env.local.example` предназначены только для
локального запуска. Переменные `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`,
`S3_SECRET_KEY`, `S3_BUCKET` и `S3_REGION` в этом шаблоне уже указывают на
локальный MinIO bucket `nmc-dev`.

App-сервисы issue #247 собираются тем же `infra/docker/service.Dockerfile`, что
и GHCR pipeline, и получают `SERVICE_NAME`, `SERVICE_PATH`, build metadata и
локальные секреты из compose/env-шаблона. Внутри compose-сети каждый сервис
слушает `APP_PORT=7700`, имеет `healthcheck` на `/health`, `read_only: true`,
`tmpfs` для `/tmp` и `/app/logs`, `security_opt: no-new-privileges:true`,
`cap_drop: ALL` и `depends_on` на healthy-инфраструктуру. Host-порты `7701`-
`7714` нужны только для одновременного локального доступа без конфликта за
один порт.

## Service discovery

Вопрос [#295](https://github.com/xlabtg/Media_Center/issues/295) закрыт:
локальная среда использует Docker Compose DNS, без отдельного service registry.
Контейнеры находят зависимости по именам compose-сервисов, а не через
опубликованные на host порты. Host-порты `7701`-`7714` нужны только для доступа
разработчика из браузера или curl на машине.

| Клиент внутри compose-сети | Endpoint |
|----------------------------|----------|
| PostgreSQL | `postgres:5432` через `DATABASE_URL` |
| Redis | `redis:6379` через `REDIS_URL` |
| RabbitMQ | `rabbitmq:5672` через `RABBITMQ_URL` |
| ChromaDB | `chroma:8000` через `CHROMA_HOST`/`CHROMA_PORT` |
| MinIO | `http://minio:9000` через `S3_ENDPOINT_URL` |
| OpenTelemetry Collector | `http://otel-collector:4318` через `OTEL_EXPORTER_OTLP_ENDPOINT` |
| Product service | `http://<service>:7700`, например `http://api-gateway:7700` |

S2S credentials не являются механизмом discovery: они подтверждают identity
вызывающего сервиса после выбора endpoint из env. Полный контракт описан в
[docs/SERVICE_DISCOVERY.md](../../docs/SERVICE_DISCOVERY.md).

Prometheus автоматически читает конфигурацию из
`infra/observability/prometheus/prometheus.yml`, включая DORA recording rules
issue #251, Grafana подключает datasource и дашборды из
`infra/observability/grafana/`, а OpenTelemetry Collector принимает
traces/logs/metrics через OTLP. Логи, метрики, traces и DORA events обязаны
содержать только технические labels и не должны включать ПДн, токены, сырое
содержимое или суммы выплат. Источники DORA-метрик описаны в
`docs/case-studies/issue-213/metrics/dora-data-sources.md`.

## Миграции, сиды и фикстуры

`make migrate` применяет:

1. `infra/local/postgres/migrations/001_dev_schema.sql` — базовую
   tenant-aware схему `nmc_dev`;
2. `infra/local/postgres/seeds/001_dev_seed.sql` — идемпотентные dev-сиды для
   пилотного tenant, участников, событий вклада и audit hash-записей;
3. dev bucket `nmc-dev` в MinIO.

JSON-фикстуры для будущих сервисных и интеграционных тестов лежат в
`infra/local/fixtures/dev-fixtures.json`. Для пилотного запуска issue #91
добавлена отдельная проверяемая фикстура
`infra/local/fixtures/pilot-tenant.json`: она описывает tenant `nmc-pilot`,
20 синтетических участников, роли, onboarding checklist и пороги Совета.
SQL-сиды намеренно не содержат ПДн, денежных сумм, токенов или реальных внешних
идентификаторов.

## Проверка

`make test` запускает `experiments/validate_issue10_local_env.sh`. Скрипт
проверяет наличие compose, env-шаблона, Makefile-таргетов, SQL-миграции,
сидов, фикстур и документации. Если Docker доступен, дополнительно выполняется
`docker compose config --quiet`.

Контракт приложенческого compose-слоя закреплён в
`tests/test_local_app_compose_issue247_contract.py`:

```bash
python -m pytest tests/test_local_app_compose_issue247_contract.py
```

## Backup/DR smoke-проверка

Backup/DR-контур issue #99 описан в
[docs/DISASTER_RECOVERY.md](../../docs/DISASTER_RECOVERY.md), а структурная
политика хранится в `infra/backup/backup-policy.json`. Для локальной проверки
без изменения docker volumes:

```bash
make backup-policy
make backup-local
make restore-drill
```

Фактический backup использует `infra/backup/scripts/backup.sh all`, а restore
drill выполняется только в изолированном sandbox, чтобы не перезаписать рабочие
PostgreSQL, ChromaDB и MinIO volumes.
