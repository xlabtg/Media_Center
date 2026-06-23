# Бюджет размера и cold-start сервисных образов

Документ фиксирует операционный бюджет REQ-N1 для сервисных Docker-образов и
REQ-N2 для cold-start до `/ready`. Исходный замер issue #232 и действующий
F2-гейт issue #252 опираются на архитектурное решение
[ADR-0008](../adr/0008-container-image-size-optimization.md).

## Бюджет REQ-N1

| Метрика | Порог | Статус |
|---------|-------|--------|
| Базовый бюджет | **< 250 МБ** на сервисный runtime-образ | Целевой fail-гейт F2. |
| Stretch-бюджет | **< 200 МБ** на сервисный runtime-образ | Цель оптимизации после базового гейта. |
| Cold-start | **< 3 с** от запуска контейнера до HTTP 200 на `/ready` | Целевой fail-гейт F2 для REQ-N2. |

Основной показатель — локальный `SIZE` из `docker image ls` или `docker system
df -v` после сборки образа. Он отражает footprint образа на узле после
загрузки и распаковки слоев. Сжатый размер registry artifact фиксируется
дополнительно, но не заменяет основной бюджет.

`docker image inspect --format '{{.Size}}'` не используется как основной
источник для Docker 29/BuildKit image store: в локальной проверке он показал
121.83 МБ для image object, тогда как `docker image ls` и `docker system df -v`
показали локальный footprint 514 МБ.

Числовые пороги для CI зафиксированы в
[`docs/operations/service-performance-budgets.json`](service-performance-budgets.json):
`250000000` байт для размера образа, stretch `200000000` байт и `3000` мс для
cold-start. Reusable workflow
`.github/workflows/build-service.yml` запускает
`.github/scripts/check_service_performance_budget.py` сразу после локальной
amd64-сборки `media-center-<service>:trivy-scan`.

Скрипт:

- читает размер из `docker image ls`, а не из `docker image inspect`;
- стартует контейнер через `docker run` и проверяет `/ready` внутри контейнера;
- очищает `DATABASE_URL`, `REDIS_URL` и `RABBITMQ_URL`, чтобы `/ready` измерял
  startup самого сервиса без ожидания внешней инфраструктуры;
- ждёт первый HTTP 200 от `/ready`;
- пишет `performance-reports/service-performance-<service>.json`;
- добавляет таблицу в `GITHUB_STEP_SUMMARY`;
- завершает job с ошибкой при `SIZE >= 250 МБ` или cold-start `> 3000` мс.

## Команда замера

```bash
docker build --provenance=false \
  -f infra/docker/service.Dockerfile \
  --build-arg SERVICE_NAME=contribution-ledger \
  --build-arg SERVICE_PATH=services/contribution-ledger \
  --build-arg SERVICE_VERSION=issue232-baseline \
  --build-arg BUILD_DATE="$(date -u +'%Y-%m-%dT%H:%M:%SZ')" \
  --build-arg GIT_COMMIT="$(git rev-parse HEAD)" \
  --build-arg GIT_TAG= \
  --build-arg IMAGE_SOURCE=https://github.com/xlabtg/Media_Center \
  -t media-center-contribution-ledger:issue232-baseline \
  .

docker image ls media-center-contribution-ledger:issue232-baseline
docker system df -v
docker save media-center-contribution-ledger:issue232-baseline \
  -o /tmp/media-center-contribution-ledger-issue232.tar
```

## Базовый замер

| Поле | Значение |
|------|----------|
| Дата замера | 2026-06-22 |
| Платформа | linux/amd64 |
| Docker CLI / Engine | 29.6.0 / 29.5.0 |
| Образ | `media-center-contribution-ledger:issue232-baseline` |
| Runtime base | `python:3.13.14-slim` |
| Локальный размер `docker image ls` | **514 МБ** |
| Shared size `docker system df -v` | 175.6 МБ |
| Unique size `docker system df -v` | 338.5 МБ |
| Архив `docker save` | 117 МБ |
| Статус к базовому бюджету | Превышает `< 250 МБ`; нужен follow-up до fail-гейта F2. |

Причина превышения: сервисный `infra/docker/service.Dockerfile` пока собирает
общий venv из всего списка `[project].dependencies` в `pyproject.toml` и
копирует его в каждый runtime-образ. Multi-stage сборка убирает build-time
слои из финального образа, но для достижения бюджета нужно разделить
runtime-зависимости по сервисам или ввести другой slim-механизм сборки без
тяжелых неиспользуемых пакетов.

## Runtime-зависимости F2

Начиная с issue #252 Dockerfile больше не устанавливает весь общий список
`[project].dependencies` в каждый сервисный образ. Вместо этого builder stage
формирует `/tmp/requirements-runtime.txt` из optional dependency groups:

- `runtime-core` — общий минимум для FastAPI runtime, `/health`, `/ready`,
  `/info`, `/metrics`, БД/Redis/RabbitMQ readiness и S2S shared-secret/JWT;
- `runtime-<SERVICE_NAME>` — service-specific добавки, если группе нужен
  отдельный пакет. Сейчас `runtime-messenger-adapter` оставлен пустым: тяжелые
  Telegram/crypto-коннекторы не нужны для базового `/ready` runtime contract и
  подключаются лениво в доменных модулях.

Тяжелые интеграции `boto3`, `chromadb-client`, `cryptography` и `Telethon`
остаются в `[project].dependencies` для разработки, тестов и audit-контекста,
но не входят в базовый runtime-образ: соответствующие backend-клиенты и
платформенные коннекторы импортируют их лениво только при фактическом
использовании.

## Правила F2-гейта

- Измерять каждый сервис из CI matrix тем же способом, которым собирается
  production runtime image.
- Падать при `SIZE >= 250 МБ`, если для сервиса нет принятого исключения.
- Падать при cold-start до `/ready` больше `3000` мс.
- Публиковать в job summary фактический размер, превышение/запас и ссылку на
  ADR-0008.
- Отдельно показывать прогресс к stretch-бюджету `< 200 МБ`.
- Прикладывать JSON-отчеты как GitHub Actions artifact `service-performance-*`
  из каталога `performance-reports`.
