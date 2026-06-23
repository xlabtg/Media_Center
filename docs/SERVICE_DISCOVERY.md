# Service discovery

- **Связанное issue:** [#295](https://github.com/xlabtg/Media_Center/issues/295)
- **Статус:** Вопрос закрыт: baseline использует DNS рантайма и явные endpoint
  из env/Helm values, без отдельного service registry.
- **Связанные документы:** [ARCHITECTURE.md](ARCHITECTURE.md),
  [contracts/sync-api.md](contracts/sync-api.md), [S2S_AUTH.md](S2S_AUTH.md),
  [../infra/local/README.md](../infra/local/README.md)

## Решение

Сервисы НМЦ не ищут зависимости через Consul/Eureka или собственный discovery
API. Адрес зависимости является частью конфигурации окружения: клиент получает
endpoint из env, secret/config provider или Helm values, а имя хоста в endpoint
резолвится DNS механизмом текущего рантайма.

S2S credentials не являются механизмом discovery. `SERVICE_NAME`,
`X-S2S-Service`, Kubernetes ServiceAccount token, RSA JWT или shared secret
доказывают identity вызывающего сервиса, но не выбирают адрес downstream.

## Локальный Docker Compose

В `infra/local/docker-compose.yml` все контейнеры находятся в одной compose-сети.
Docker Compose DNS публикует каждый сервис по имени ключа из `services`, поэтому
app-контейнеры используют внутренние адреса:

| Зависимость | Внутренний endpoint |
| --- | --- |
| PostgreSQL | `postgres:5432` через `DATABASE_URL` |
| Redis | `redis:6379` через `REDIS_URL` |
| RabbitMQ | `rabbitmq:5672` через `RABBITMQ_URL` |
| ChromaDB | `chroma:8000` через `CHROMA_HOST`/`CHROMA_PORT` |
| MinIO | `http://minio:9000` через `S3_ENDPOINT_URL` |
| OpenTelemetry Collector | `http://otel-collector:4318` через `OTEL_EXPORTER_OTLP_ENDPOINT` |
| Product service | `http://<service>:7700` |

Host-порты из `infra/local/.env.local.example`, например `7701`-`7714`, нужны
только разработчику на машине. Контейнеры не должны ходить друг к другу через
`localhost:<host-port>`: внутри контейнера `localhost` указывает на сам
контейнер. Готовность инфраструктуры в compose синхронизируется через
`depends_on` с `condition: service_healthy`, но это не заменяет retry/timeout в
клиентском коде.

Пример внутреннего HTTP-вызова в локальной сети:

```text
http://api-gateway:7700/health
http://notification-gateway:7700/notify
```

## Kubernetes и Helm

Chart `deploy/helm/media-center` для каждого включенного продукта создаёт
Kubernetes Service типа `ClusterIP` на порту `7700`. Имя сервиса формируется
шаблоном:

```text
<release>-media-center-<service>
```

Например, для release `nmc` и сервиса `api-gateway` стабильный DNS target:

```text
nmc-media-center-api-gateway.<namespace>.svc.cluster.local:7700
```

Внутри namespace обычно достаточно короткого имени
`nmc-media-center-api-gateway:7700`. Cross-namespace вызовы должны использовать
FQDN или явно настроенный endpoint. Внешние клиенты продолжают входить через API
Gateway; прямой доступ к product service допустим только внутри приватной
сервисной сети и с service-to-service авторизацией.

## Правила конфигурации

- Source of truth для адреса зависимости - env/values конкретного окружения:
  `DATABASE_URL`, `REDIS_URL`, `RABBITMQ_URL`, `CHROMA_HOST`,
  `S3_ENDPOINT_URL`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `BLOCKCHAIN_AUDITOR_URL` и
  будущие service-specific `*_URL`.
- В коде не должно быть hard-coded `localhost` для межконтейнерного или
  production-вызова. `localhost` допустим только для healthcheck внутри того же
  контейнера, локального host-шаблона или тестового in-memory сценария.
- Новый синхронный downstream добавляет явную env/values переменную и
  документирует owner, протокол, порт, auth method и timeout/retry ожидания.
- API Gateway маршрутизирует внешние HTTP-запросы по service prefix. Внутренние
  сервисы могут вызывать product service напрямую только по настроенному
  endpoint и с S2S credentials.
- Если потребуется динамическая маршрутизация за пределами DNS рантайма,
  решение оформляется отдельным ADR. До такого ADR отдельный service registry
  не является частью baseline.

## Проверка

Контракт #295 закреплён в
`tests/test_service_discovery_issue295_contract.py`. Он проверяет, что:

- документация явно закрывает вопрос discovery;
- локальный compose использует DNS-имена зависимостей, а не host-порты;
- Helm chart создаёт стабильные `ClusterIP` Service для product services;
- S2S identity не смешивается с выбором endpoint.
