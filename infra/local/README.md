# Локальная среда разработки

`infra/local` содержит воспроизводимый docker-compose для разработки и
smoke-проверок платформы НМЦ. Стек поднимает PostgreSQL, Redis, RabbitMQ,
ChromaDB, MinIO, Prometheus, Grafana и OpenTelemetry Collector с версиями из
[ADR-0006](../../docs/adr/0006-technology-stack-and-versions.md) и локального
observability baseline.

## Быстрый старт

Требования:

- Docker с compose plugin;
- свободные локальные порты `5432`, `6379`, `5672`, `15672`, `8001`, `9000`,
  `9001`, `9090`, `3000`, `4317`, `4318`, `8889`.

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

Dev-логины из `infra/local/.env.local.example` предназначены только для
локального запуска. Переменные `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`,
`S3_SECRET_KEY`, `S3_BUCKET` и `S3_REGION` в этом шаблоне уже указывают на
локальный MinIO bucket `nmc-dev`.

Prometheus автоматически читает конфигурацию из
`infra/observability/prometheus/prometheus.yml`, Grafana подключает datasource и
дашборд из `infra/observability/grafana/`, а OpenTelemetry Collector принимает
traces/logs/metrics через OTLP. Логи, метрики и traces обязаны содержать
`tenant_id` и не должны включать ПДн, токены, сырое содержимое или суммы выплат.

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
