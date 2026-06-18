# Infra

**Статус:** каркас инфраструктуры с базовой CI-сборкой сервисных образов и
локальной docker-compose средой.

## Назначение

`infra` хранит инфраструктурные артефакты, которые не относятся к коду
конкретного сервиса: локальную среду, deploy-конфигурации, observability и
операционные настройки.

## Подкаталоги

| Каталог | Назначение |
|---------|------------|
| `local/` | docker-compose для локальной разработки и smoke-проверок. |
| `deploy/` | Будущие deployment-манифесты и окружения. |
| `observability/` | Будущие конфигурации Prometheus, Grafana, логов и трейсинга. |
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
ChromaDB и MinIO с фиксированными версиями из ADR-0006. Основной workflow:

```bash
make up
make migrate
make test
make down
```

Подробности, порты, env-шаблон, миграции, сиды и фикстуры описаны в
[local/README.md](local/README.md).

## Правила

- docker-compose и образы используют явные версии из
  [ADR-0006](../docs/adr/0006-technology-stack-and-versions.md), без `latest`.
- Секреты не коммитятся; допустимы только примеры и ссылки на `.env.example`.
- Observability-конфигурация не должна раскрывать ПДн, токены и суммы выплат.
