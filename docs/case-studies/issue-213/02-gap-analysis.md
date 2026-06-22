# 02. Gap-анализ: текущее состояние репозитория против требований

Анализ выполнен по коду ветки на момент 22 июня 2026. Для каждого требования — фактическое состояние с ссылками на файлы и вывод (✅ есть / 🟡 частично / ❌ нет).

## Сводная таблица

| REQ | Требование | Статус | Где сейчас / чего не хватает |
| --- | --- | --- | --- |
| REQ-1 | Multi-stage + минимальный runtime | ❌ | `infra/docker/service.Dockerfile` — одностадийный stub |
| REQ-2 | Готовый entrypoint | ❌ | `CMD` печатает «image is ready», приложение не стартует |
| REQ-3 | Единый порт 7700 | ❌ | `libs/shared/config.py` → `app_port=8000` |
| REQ-4 | `/health` | 🟡 | Есть в `libs/shared/service_template.py`, но не во всех сервисах и не в образе |
| REQ-5 | `/info` (build metadata) | ❌ | Нет endpoint, нет `build_info.json` |
| REQ-6 | `/metrics` | 🟡 | Есть в `service_template.py`, не унифицирован |
| REQ-7 | Логи в консоль | 🟡 | Нет общего `logging_config`, формат не JSON, не централизован |
| REQ-8 | Runtime log-level | ❌ | Нет endpoint смены уровня |
| REQ-9 | Публикация в GHCR | 🟡 | `ci.yml` job `images` пушит, но без semver-тегов из git |
| REQ-10 | S2S auth | ❌ | Есть только tenant JWT (HS256) для пользователей, нет S2S |
| REQ-11 | Единый путь артефакта | ❌ | Образ копирует только README, единой структуры `/app/service` нет |
| REQ-12 | Non-root + hardening | 🟡 | `USER app` есть; нет read-only/no-new-privileges/tini/caps |
| REQ-N1..N5 | Метрики превосходства | ❌ | Нет бюджетов размера/cold-start, SBOM, подписи, DORA, SLO |

## Детально по требованиям

### REQ-1 / REQ-11 — Dockerfile и структура артефакта ❌

Текущий `infra/docker/service.Dockerfile` (28 строк) — заглушка для CI:

```dockerfile
FROM python:3.13.14-slim
...
COPY --chown=app:app ${SERVICE_PATH}/README.md ./SERVICE.md
COPY --chown=app:app libs/shared/README.md ./SHARED.md
USER app
CMD ["python", "-c", "import os; print(... + ' image is ready')"]
```

- Одностадийный (нет builder/runtime разделения) → **REQ-1.1/1.2 не выполнены**.
- Не устанавливаются зависимости, не копируется код сервиса → образ ничего не запускает.
- Нет `/app/service` + `/app/config` → **REQ-11 не выполнено**.
- Есть `USER app` (частично REQ-12), но нет `tini`, `HEALTHCHECK`, entrypoint.

**Положительное:** уже заданы OCI-метки `org.opencontainers.image.*`, `PYTHONDONTWRITEBYTECODE`, `PYTHONUNBUFFERED` — переиспользуем.

### REQ-2 — Entrypoint ❌

- В образе нет `entrypoint.sh`; `CMD` лишь печатает строку.
- В `services/contribution-ledger/contribution_ledger_app/main.py` определён `app = build_app()`, но **нет ASGI-раннера** (`uvicorn`/`python -m ... .main`), который поднимал бы сервер на 7700. То есть даже корректный образ не знал бы, чем стартовать.

### REQ-3 — Единый порт 7700 ❌

`libs/shared/config.py`, `AppSettings`:
- `app_port: int = 8000` (нужно 7700, REQ-3.1).
- `app_host: str = "0.0.0.0"` — ок.
- `log_level: str = "INFO"` — ок (REQ-8.2), `LOG_LEVELS = {DEBUG, INFO, WARNING, ERROR}` — но без `CRITICAL`.

### REQ-4 / REQ-6 — `/health`, `/metrics` 🟡

`libs/shared/service_template.py`, `create_service_app()`:
- `GET /health` → `{service, version, status, checks{database, metrics}}`. Это **liveness+readiness вместе**, разделения `/ready` нет.
- `GET /metrics` (через `DEFAULT_METRICS_PATH`) → Prometheus экспозиция `TenantMetricRegistry`.
- **Проблема:** не все 14 сервисов используют `create_service_app`; единый контракт endpoints в образ не попадает (образ не запускает приложение).

### REQ-5 — `/info` ❌

- Нет endpoint `/info`.
- `ServiceTemplateConfig.version` хардкодится (`"0.1.0"`/`"0.1.0"`), не из git-тега.
- Нет генерации `build_info.json` на сборке (дата, python-версия, commit, tag).

### REQ-7 / REQ-8 — Логирование 🟡 / ❌

- В `libs/shared/` **нет** `logging_config.py`. Логирование не централизовано, не JSON, не гарантирован stdout.
- Нет endpoint `PUT /admin/log-level` (**REQ-8.1 ❌**).
- Уровень при старте берётся из `config.log_level` (**REQ-8.4 частично ок**), но access-log поведение (**REQ-8.3**) не закреплено.

### REQ-9 — GHCR 🟡

`.github/workflows/ci.yml`, job `images`:
- Matrix из 10 сервисов, сборка `infra/docker/service.Dockerfile`.
- Пуш `ghcr.io/${owner}/media-center-${service}:${sha}` и `:latest` (только push в main).
- **Чего нет:** semver-теги из git (`git describe`/`metadata-action`), build-args (`BUILD_DATE/GIT_COMMIT/GIT_TAG/SERVICE_VERSION`), multi-arch (arm64), SBOM/подпись/provenance, скан самого образа (Trivy сейчас только `fs`).
- **Расхождение имён:** CI использует префикс `media-center-`, источник Qwen предлагает `nmc-`. **Решение:** сохраняем `media-center-` (обратная совместимость), фиксируем как ADR.

### REQ-10 — S2S auth ❌

- `libs/shared/config.py` + `libs/shared/auth.py`: JWT (HS256) для пользовательских токенов, tenant-контекст. Есть Vault-интеграция (`VaultSettings`, `VaultSecretProvider`).
- **Нет** `libs/shared/s2s_auth.py`, нет цепочки k8s SA → RSA → secret, нет проверки S2S на служебных endpoint. Сейчас доверие между сервисами — по внутренним заголовкам (Internal Headers Trust).

### REQ-12 — Non-root + hardening 🟡

- `USER app` в Dockerfile — ✅ (REQ-12.1).
- Нет `tini` (PID 1 / сигналы), нет `read_only` rootfs, нет `no-new-privileges`, нет drop capabilities, нет `tmpfs` для writable-путей — **REQ-12.2 ❌**.
- `infra/local/docker-compose.yml` поднимает только инфраструктуру (postgres, redis, rabbitmq, chroma, minio, prometheus, alertmanager, grafana, otel) — **приложенческие сервисы не запускаются вовсе**, hardening-флаги негде применить.

### REQ-N1..N5 — Метрики превосходства ❌

- Нет бюджетов размера образа и cold-start, нет гейтов в CI.
- Нет SBOM (Syft), подписи (cosign), provenance (SLSA).
- Нет измерения DORA-метрик, нет формализованных SLO/error budget для сервисов.

## Вывод

Контейнеризационный слой репозитория находится в состоянии **«каркас/планирование»**: общий шаблон приложения (`service_template.py`) даёт хорошую основу для `/health` и `/metrics`, есть Vault и наблюдаемость, но **образ ничего не запускает**, отсутствуют `/info`, `/ready`, runtime log-level, S2S-auth, multi-stage сборка, hardening и метрики поставочного превосходства.

Это формирует объём **«Этапа 9»**: довести контейнеризацию до production-grade и закрыть все 12 требований источника + 5 нефункциональных, переиспользуя уже имеющиеся `service_template.py`, OCI-метки, Vault, Prometheus/OTel, матричный CI.
