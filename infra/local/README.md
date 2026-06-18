# Локальная среда разработки

`infra/local` содержит воспроизводимый docker-compose для разработки и
smoke-проверок платформы НМЦ. Стек поднимает PostgreSQL, Redis, RabbitMQ,
ChromaDB и MinIO с версиями из [ADR-0006](../../docs/adr/0006-technology-stack-and-versions.md).

## Быстрый старт

Требования:

- Docker с compose plugin;
- свободные локальные порты `5432`, `6379`, `5672`, `15672`, `8001`, `9000`,
  `9001`.

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

## Сервисы и порты

| Сервис | Образ | Локальный адрес |
|--------|-------|-----------------|
| PostgreSQL | `postgres:17` | `localhost:5432` |
| Redis | `redis:7.4` | `localhost:6379` |
| RabbitMQ | `rabbitmq:4.1-management` | AMQP `localhost:5672`, UI `http://localhost:15672` |
| ChromaDB | `chromadb/chroma:1.5.9` | `http://localhost:8001` |
| MinIO | `minio/minio:RELEASE.2025-09-07T16-13-09Z` | API `http://localhost:9000`, UI `http://localhost:9001` |

Dev-логины из `infra/local/.env.local.example` предназначены только для
локального запуска.

## Миграции, сиды и фикстуры

`make migrate` применяет:

1. `infra/local/postgres/migrations/001_dev_schema.sql` — базовую
   tenant-aware схему `nmc_dev`;
2. `infra/local/postgres/seeds/001_dev_seed.sql` — идемпотентные dev-сиды для
   пилотного tenant, участников, событий вклада и audit hash-записей;
3. dev bucket `nmc-dev` в MinIO.

JSON-фикстуры для будущих сервисных и интеграционных тестов лежат в
`infra/local/fixtures/dev-fixtures.json`. SQL-сиды намеренно не содержат ПДн,
денежных сумм, токенов или реальных внешних идентификаторов.

## Проверка

`make test` запускает `experiments/validate_issue10_local_env.sh`. Скрипт
проверяет наличие compose, env-шаблона, Makefile-таргетов, SQL-миграции,
сидов, фикстур и документации. Если Docker доступен, дополнительно выполняется
`docker compose config --quiet`.
