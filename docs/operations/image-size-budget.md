# Бюджет размера сервисных образов

Документ фиксирует операционный бюджет REQ-N1 для сервисных Docker-образов и
исходный замер issue #232. Архитектурное решение принято в
[ADR-0008](../adr/0008-container-image-size-optimization.md).

## Бюджет REQ-N1

| Метрика | Порог | Статус |
|---------|-------|--------|
| Базовый бюджет | **< 250 МБ** на сервисный runtime-образ | Целевой fail-гейт F2. |
| Stretch-бюджет | **< 200 МБ** на сервисный runtime-образ | Цель оптимизации после базового гейта. |

Основной показатель — локальный `SIZE` из `docker image ls` или `docker system
df -v` после сборки образа. Он отражает footprint образа на узле после
загрузки и распаковки слоев. Сжатый размер registry artifact фиксируется
дополнительно, но не заменяет основной бюджет.

`docker image inspect --format '{{.Size}}'` не используется как основной
источник для Docker 29/BuildKit image store: в локальной проверке он показал
121.83 МБ для image object, тогда как `docker image ls` и `docker system df -v`
показали локальный footprint 514 МБ.

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

## Правила будущего F2-гейта

- Измерять каждый сервис из CI matrix тем же способом, которым собирается
  production runtime image.
- Падать при `SIZE >= 250 МБ`, если для сервиса нет принятого исключения.
- Публиковать в job summary фактический размер, превышение/запас и ссылку на
  ADR-0008.
- Отдельно показывать прогресс к stretch-бюджету `< 200 МБ`.
